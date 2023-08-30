"""
Methods for the CloudFormation XUI that need to be used in different actions
are stored in this module.
"""
import json
import time
from decimal import Decimal

import base64

import html
import requests
import urllib.parse
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.template import Context, Template

from behavior_mapping.models import SequencedItem, CustomFieldMapping
from cbhooks.models import CloudBoltHook, HookPoint, HookPointAction, \
    OrchestrationHook
from common.methods import set_progress
from infrastructure.models import Environment, CustomField, FieldDependency, \
    Namespace, Server
from orders.models import CustomFieldValue
from resources.models import ResourceType
from servicecatalog.models import RunCloudBoltHookServiceItem, \
    TearDownServiceItem
from tags.models import CloudBoltTag
from utilities.events import add_server_event
from utilities.exceptions import CloudBoltException, NotFoundException
from utilities.logger import ThreadLogger
from utilities.models import ConnectionInfo
from utilities.run_command import execute_command

logger = ThreadLogger(__name__)


def get_supported_conn_info_labels():
    # Returns a tuple of conn_info_types, label_queries, conn_info_queries
    # label_queries used when filtering labels, conn_info
    conn_info_types = ["github", "gitlab", "azure_devops"]
    conn_info_queries = []
    for l in conn_info_types:
        conn_info_queries.append(f'Q(labels__name="{l}")')
    conn_info_queries = " | ".join(conn_info_queries)
    label_queries = []
    for l in conn_info_types:
        label_queries.append(f'Q(name="{l}")')
    label_queries = " | ".join(label_queries)
    return conn_info_types, label_queries, conn_info_queries


def get_conn_info_type(conn_info):
    _, queries, _ = get_supported_conn_info_labels()
    labels = conn_info.labels.filter(eval(queries))
    if len(labels) > 1:
        raise CloudBoltException(
            f"More than one valid label found on {conn_info} Connection."
        )
    if len(labels) == 0:
        raise CloudBoltException(
            f"No valid source control labels found on conn info: {conn_info}."
        )
    conn_info_type = labels.first().name
    logger.debug(
        f"Connection info: {conn_info.name} determined to be type of "
        f"{conn_info_type}"
    )
    return conn_info_type


def generate_options_for_resource_type(**kwargs):
    rts = ResourceType.objects.filter(lifecycle='ACTIVE')
    initial_rt = kwargs.get("initial_rt")
    options = []
    if initial_rt:
        options.append((initial_rt,
                        ResourceType.objects.get(id=initial_rt).label))
    for rt in rts:
        if rt.id == initial_rt:
            continue
        options.append((rt.id, rt.label))

    return options


def generate_options_for_connection_info(server=None, **kwargs):
    _, _, queries = get_supported_conn_info_labels()
    cis = ConnectionInfo.objects.filter(eval(queries))
    initial_ci = kwargs.get("initial_ci")
    options = [(0, "Public Repository")]
    logger.info(f'arm - initial_ci: {initial_ci}')
    if initial_ci:
        if int(initial_ci) != 0:
            initial = (
            initial_ci, ConnectionInfo.objects.get(id=initial_ci).name)
            options.insert(0, initial)

    for ci in cis:
        if ci.name == "CloudBolt Content Library":
            continue
        if ci.id == initial_ci:
            continue
        ci_type = get_conn_info_type(ci)
        options.append((ci.id, f"{ci.name}: {ci_type}"))
    return options


def generate_options_for_allowed_environments(server=None, **kwargs):
    options = [("all_capable", " All Capable")]
    envs = Environment.objects.filter(
        resource_handler__resource_technology__modulename__contains="aws"
    )
    options += [(env.id, env.name) for env in envs]
    return options


def create_param_name(param_prefix, bp_id):
    return f"{param_prefix}_{bp_id}"


def create_custom_field_option(blueprint, value, field, cf_type):
    """
    Create a CustomFieldValue for a Custom Field, then add that value to the
    Blueprint level Parameter. This will check first to see if the value
    already exists for that parameter on the blueprint, and if so, not re-add
    to the Blueprint
    """
    logger.debug(
        f"Creating Parameter: {field.name} option, type: {cf_type}," f" Value: {value}"
    )
    if cf_type == "STR":
        cfv = CustomFieldValue.objects.get_or_create(str_value=value,
                                                     field=field)[0]
    elif cf_type == "INT":
        cfv = CustomFieldValue.objects.get_or_create(int_value=value,
                                                     field=field)[0]
    elif cf_type == "BOOL":
        cfv = CustomFieldValue.objects.get_or_create(boolean_value=value,
                                                     field=field)[0]
    elif cf_type == "CODE":
        cfv = CustomFieldValue.objects.get_or_create(txt_value=value,
                                                     field=field)[0]
    else:
        logger.warn(
            f"Unknown Parameter type: {cf_type}, passed in, not "
            f"creating custom field option for field: {field}"
        )
        return None
    cfvs = blueprint.get_cfvs_for_custom_field(field.name)
    if cfv not in cfvs:
        # Don't want to re-add a value if it already exists on the BP
        blueprint.custom_field_options.add(cfv.id)
    return cfv


def add_cfvs_for_field(blueprint, cf, cf_type, new_values: list):
    """
    Add new values to the blueprint for a field, remove CFVs that are not in
    new_values
    """
    existing_cfvs = blueprint.custom_field_options.filter(field__id=cf.id)
    new_cfvs = []
    for value in new_values:
        cfv = create_custom_field_option(blueprint, value, cf, cf_type)
        if cfv:
            new_cfvs.append(cfv)
    remove_old_cfvs(blueprint, existing_cfvs, new_cfvs)


def remove_old_cfvs(blueprint, existing_cfvs, new_cfvs):
    for cfv in existing_cfvs:
        if cfv not in new_cfvs:
            logger.debug(f'Removing CustomFieldValue: {cfv} from options')
            blueprint.custom_field_options.remove(cfv)


def create_cf(
        cf_name,
        cf_label,
        description,
        cf_type="STR",
        allow_multiple=False,
        required=True,
        **kwargs,
):
    namespace, _ = Namespace.objects.get_or_create(name="aws_cloudformation")

    # You can pass in show_on_servers, show_as_attribute as kwargs
    defaults = {
        "label": cf_label,
        "description": description,
        "required": required,
        "allow_multiple": allow_multiple,
        "namespace": namespace,
    }
    for key, value in kwargs.items():
        defaults[key] = value

    cf = CustomField.objects.get_or_create(
        name=cf_name, type=cf_type, defaults=defaults
    )
    return cf


def create_cloudbolt_hook(new_action_name, source_file):
    root_dir = "/var/opt/cloudbolt/proserv/xui/cloud_formation/actions/"
    hook, hook_created = CloudBoltHook.objects.get_or_create(
        name=new_action_name, source_code_url=f"file://{root_dir}{source_file}"
    )
    hook.get_runtime_module()
    hook.description = "Used for CloudFormation Builder"
    hook.shared = True
    hook.save()
    if hook_created:
        set_progress(f"CloudBolt Hook created: {new_action_name}")
    else:
        set_progress(f"CloudBolt Hook retrieved: {new_action_name}")
    return hook


def create_generated_options_action(new_action_name, source_file):
    # Create Action (CloudBoltHook)
    hook = create_cloudbolt_hook(new_action_name, source_file)

    # Create HookPointAction
    hp_id = HookPoint.objects.get(name="generated_custom_field_options").id
    hpa = HookPointAction.objects.get_or_create(
        name=new_action_name, hook=hook, hook_point_id=hp_id
    )[0]
    hpa.enabled = True
    hpa.continue_on_failure = False
    hpa.save()

    oh = OrchestrationHook.objects.get_or_create(
        name=new_action_name, cloudbolthook=hook
    )[0]
    oh.hookpointaction_set.add(hpa)
    oh.save()

    return oh


def create_param_label(param):
    param_label = " ".join(camel_case_split(param)).title()
    # Handles QuickStart templates where underscores are used
    param_label = param_label.replace("_", " ")
    return param_label


def camel_case_split(string):
    words = [[string[0]]]
    for c in string[1:]:
        if words[-1][-1].islower() and c.isupper():
            words.append(list(c))
        else:
            words[-1].append(c)
    return ["".join(word) for word in words]


def get_cft_from_source(connection_info_id, url):
    if int(connection_info_id) != 0:
        conn_info = ConnectionInfo.objects.get(id=connection_info_id)
        conn_info_type = get_conn_info_type(conn_info)
    else:
        conn_info = None
        conn_info_type = 'public'
    function_call = f'get_template_from_{conn_info_type}(conn_info, url)'
    cft = eval(function_call)
    if not cft:
        raise Exception(
            f"CFT could not be found for conn_info: {conn_info}, and "
            f"URL: {url}")
    return cft


def get_template_from_azure_devops(conn_info, url):
    try:
        if url.find("/_git/") > -1:
            raw_url = generate_raw_ado_url(url)
        else:
            raw_url = url
    except Exception as e:
        raise Exception("Raw URL could not be determined for Azure DevOps File"
                        f". Error: {e}")
    username = conn_info.username
    token = conn_info.password
    user_pass = f'{username}:{token}'
    b64 = base64.b64encode(user_pass.encode()).decode()
    headers = {"Authorization": f"Basic {b64}"}
    response = requests.get(raw_url, headers=headers)
    response.raise_for_status()
    r_json = response.json()
    arm_template = json.dumps(r_json)
    return arm_template


def generate_raw_ado_url(file_url):
    """
    Generate the raw URL needed in CloudBolt to use the "Fetch from URL" option
    in a CloudBolt action. This method assumes that you are able to use TfsGit
    As your source provider, and that your Project and Repo Names are the same
    :param file_url: The URL of the file in Azure DevOps
    Ex: https://dev.azure.com/cloudbolt-sales-demo/_git/CMP?path=/python_samples/xaas_build.py
    """
    if file_url.find('/_git/') == -1:
        raise Exception('The URL entered appears to not be a GIT file')
    if file_url.find('?path=') == -1:
        raise Exception('The URL entered does not include a path, the URL '
                        'should point to a file')
    url_parse = urllib.parse.urlparse(file_url)
    scheme = url_parse.scheme
    server = url_parse.netloc
    url_prefix = f'{scheme}://{server}'
    url_path = url_parse.path
    org = url_path.split('/')[1]
    project = url_path.split('/')[3]
    query = url_parse.query
    args_split = query.split('&')
    branch = ''
    path = ''
    for arg in args_split:
        key, value = arg.split('=')
        if key == 'path':
            path = urllib.parse.quote(urllib.parse.unquote(value), safe='')
        if key == 'version' and value.find('GB') == 0:
            branch = value[2:]
    if not path:
        raise Exception('Path was not found in the URL entered')
    if not branch:
        branch = 'main'
    raw_url = f'{url_prefix}/{org}/{project}/_apis/sourceProviders/TfsGit/' \
              f'filecontents?repository={project}&path={path}&commitOrBranch=' \
              f'{branch}&api-version=5.0-preview.1'
    return raw_url


def get_template_from_public(conn_info, url):
    if url.find('raw') == -1:
        raise CloudBoltException(f'URL entered was not in raw format, please '
                                 f're-submit request using a raw formatted '
                                 f'URL')
    response = requests.get(url)
    response.raise_for_status()
    return response.content.decode("utf-8")


def get_template_from_gitlab(conn_info, url):
    # For gitlab, it doesn't matter if the URL passed is the Raw or the normal
    # URL. The URL needs to be reconstructed to make an API call. The token
    # created for auth will need at a minimum read_api and read_repository
    base_url = f"https://{url.split('/')[2]}:443/api/v4"
    project_path = "/".join(url.split("/-/")[0].split("/")[-2:])
    project_id = urllib.parse.quote(project_path, safe="")
    url_split = url.split("/-/")[1].split("/")
    branch = url_split[1]
    file_path = urllib.parse.quote("/".join(url_split[2:]), safe="")
    path = f"/projects/{project_id}/repository/files/{file_path}/raw" f"?ref={branch}"
    set_progress(f"Submitting request to GitLab URL: {path}")
    headers = {
        "PRIVATE-TOKEN": conn_info.password,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request_url = f"{base_url}{path}"
    r = requests.get(request_url, auth=None, headers=headers)
    r.raise_for_status()
    r_json = r.json()
    raw_file_json = json.dumps(r_json)
    return raw_file_json


def get_template_from_github(conn_info, cft_url):
    import base64
    allowed_hosts = [
        "github.com"
    ]
    url_split = cft_url.split("/")
    username = url_split[3]
    repo = url_split[4]
    host = urllib.parse.urlparse(cft_url).hostname
    if host and host in allowed_hosts:
        branch = url_split[6]
    else:
        branch = url_split[5]
    file_path = cft_url.split(f"/{branch}/")[1].split("?")[0]

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {conn_info.password}",
    }
    git_url = (
        f"https://api.github.com/repos/{username}/{repo}/contents/"
        f"{file_path}?ref={branch}"
    )
    response = requests.get(git_url, headers=headers)
    response.raise_for_status()
    data = response.json()
    content = data["content"]
    file_content_encoding = data.get("encoding")
    if file_content_encoding == "base64":
        content = base64.b64decode(content).decode()
    return content


def add_blueprint_label(blueprint):
    # Create Label if it doesn't exist
    label = CloudBoltTag.objects.get_or_create(
        name="CloudFormation", model_name="serviceblueprint"
    )[0]
    # Add Label to Blueprint
    blueprint.tags.add(label)
    return None


def create_cf_bp_options(
        cf_name,
        cf_label,
        description,
        blueprint,
        values: list,
        cf_type="STR",
        allow_multiple=False,
        required=True,
        **kwargs,
):
    # Create the Custom Field
    cf = create_cf(
        cf_name, cf_label, description, cf_type, allow_multiple, required,
        **kwargs
    )[0]

    # Add it to the Blueprint
    blueprint.custom_fields_for_resource.add(cf)

    add_cfvs_for_field(blueprint, cf, cf_type, values)
    return cf


def create_blueprint_level_params(
        blueprint, template_json, cft_url, allowed_environments, conn_info_id
):
    # Save Cloud Formation Template
    create_cf_bp_options(
        "cloud_formation_template",
        "CloudFormation Template",
        "CloudFormation Template Contents",
        blueprint,
        [template_json],
        cf_type="CODE",
    )

    # Save Allowed Environments
    env_ids = ",".join(allowed_environments)
    allowed_envs_cf = create_cf_bp_options(
        "cft_allowed_env_ids",
        "Allowed Environments",
        "A list of the IDs of Environments allowed for the " "CFT",
        blueprint,
        [env_ids],
    )

    # Save CFT URL
    create_cf_bp_options(
        "cft_url",
        "CloudFormation URL",
        "The URL where the CFT is located in Source Control",
        blueprint,
        [cft_url],
    )

    # Save Connection Info ID
    create_cf_bp_options(
        "cft_conn_info_id",
        "ConnectionInfo ID",
        "The ID of the Connection Info for Source Control",
        blueprint,
        [conn_info_id],
    )

    # Create Environment Parameter, add to Blueprint
    env_cf = create_cf_bp_options(
        "cft_env_id", "Environment", "AWS Environment", blueprint, []
    )
    SequencedItem.objects.get_or_create(custom_field=env_cf)

    # Create Programmatically Gen Options action for environment
    oh = create_generated_options_action(
        "Generate options for 'cft_env_id'", "generate_options_for_env_id.py"
    )
    env_cf.orchestration_hooks.add(oh)
    env_cf.save()
    blueprint.custom_fields_for_resource.add(env_cf)

    # Create CloudFormation Stack Name param
    cf = create_cf_bp_options(
        "cft_stack_name",
        "CloudFormation Stack Name",
        "Name of the CloudFormation Stack",
        blueprint,
        [],
        show_on_servers=True,
    )
    SequencedItem.objects.get_or_create(custom_field=cf)
    constraints = {"minimum": Decimal('1.0'), "maximum": Decimal('128.0'),
                   "regex_constraint": "^[a-zA-Z][-a-zA-Z0-9]*"}
    set_constraints_for_cf(cf, constraints)

    # Create Field Dependency for Environment to read from list of allowed envs
    create_field_dependency(allowed_envs_cf, env_cf)

    # Create CloudFormation Stack Name param
    options = ["DO_NOTHING", "ROLLBACK", "DELETE"]
    cf = create_cf_bp_options(
        "cft_fail_behavior",
        "CloudFormation Failure Behavior",
        "Failure Behavior for CloudFormation Stack Deployment",
        blueprint,
        options,
    )
    SequencedItem.objects.get_or_create(custom_field=cf)

    # Create CloudFormation Capabilities dropdown
    options = ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM",
               "CAPABILITY_AUTO_EXPAND"]
    cf = create_cf_bp_options(
        "cft_capabilities",
        "CloudFormation Capabilities",
        (
            "In some cases, you must explicitly acknowledge that your stack "
            "template contains certain capabilities in order for CloudFormation "
            "to create the stack."
        ),
        blueprint,
        options,
        allow_multiple=True,
        required=False,
        show_on_servers=True,
    )
    SequencedItem.objects.get_or_create(custom_field=cf)


def create_field_dependency(
        control_field, dependent_field, dependency_type: str = "REGENOPTIONS"
):
    dependency, _ = FieldDependency.objects.get_or_create(
        controlling_field=control_field,
        dependent_field=dependent_field,
        dependency_type=dependency_type,
    )
    return dependency


def add_bp_items(blueprint):
    # Add Build Item
    hook = create_cloudbolt_hook("Cloud Formation Build", "deploy_cft.py")
    oh, _ = OrchestrationHook.objects.get_or_create(
        name="Cloud Formation Build", cloudbolthook=hook
    )
    rcbhsi, _ = RunCloudBoltHookServiceItem.objects.get_or_create(
        name="Cloud Formation Build",
        hook=oh,
        blueprint=blueprint,
        show_on_order_form=False,
        run_on_scale_up=False,
    )

    # Add Teardown Item
    hook = create_cloudbolt_hook("Cloud Formation Teardown", "teardown_cft.py")
    oh, _ = OrchestrationHook.objects.get_or_create(
        name="Cloud Formation Teardown", cloudbolthook=hook
    )
    tdsi, _ = TearDownServiceItem.objects.get_or_create(
        name="Cloud Formation Teardown", hook=oh, blueprint=blueprint,
        deploy_seq=-1
    )


def create_params(blueprint, template_json, aws_params_hook=None):
    # import cfn_tools. If it's not installed, install it and import it.
    bp_id = blueprint.id
    template_content = get_template_content(template_json)
    template_params = template_content.get("Parameters", None)
    if template_params:
        param_prefix = f"cft_{bp_id}_"
        for key in template_params.keys():
            is_aws_param, param_type = check_aws_param(key, template_content,
                                           template_params)
            set_progress(f'is_aws_param: {is_aws_param}, key: {key}')
            create_param(key, template_params, param_prefix, blueprint,
                         param_type, aws_params_hook, is_aws_param)


def get_template_content(template_json):
    try:
        template_content = json.loads(template_json)
    except json.decoder.JSONDecodeError:
        try:
            import cfn_tools
        except ImportError or ModuleNotFoundError:
            set_progress("Installing CFN Tools")
            execute_command("pip install cfn_flip")
            import cfn_tools
        try:
            temp_data = cfn_tools.load_yaml(template_json)
            template_content = json.loads(
                json.dumps(temp_data)
            )  # converts the OrderedDict output by cfn-tools into a standard dict
        except:
            set_progress(
                "Could not Parse template, verify it is valid Yaml or JSON.")
            raise
    return template_content


def create_param(key, template_params, param_prefix, blueprint,
                 param_type, aws_params_hook=None, is_aws_param=False):
    param = template_params[key]
    new_param_name = f"{param_prefix}{key}"
    param_label = create_param_label(key)
    description = param.get("Description", "CloudFormation Builder Param")
    # TODO handle NoEcho
    # if param['NoEcho']:
    #     cf, cf_created = create_cf(new_param_name, param_label, description, 'PWD')
    #     # Do not want to set a value for passwords, just continue to next param
    #     logger.debug(f'Created Param: {new_param_name}, type: {type}, '
    #                  f'label: {param_label}')
    #     blueprint.custom_fields_for_resource.add(cf)
    #     return

    # TODO handle constraints

    allow_multiple = False
    required = True
    if param_type == "String":
        cf_type = "STR"
    elif param_type == "Number":
        cf_type = "INT"
    elif param_type == "List<Number>":
        cf_type = "INT"
        allow_multiple = True
    elif param_type == "CommaDelimitedList":
        cf_type = "STR"
        allow_multiple = True
    elif param_type.startswith("AWS::"):
        cf_type = "STR"
        new_param_name = new_param_name + "__" + param_type.replace("::", "_")
        required = True
    elif param_type == "InstanceType":
        cf_type = "STR"
        new_param_name = new_param_name + "__AWS_EC2_InstanceType_Name"
    else:
        logger.warn(
            f"Unable to find a known type for parameter: {key}."
            f"This parameter will not be considered in the created"
            f"blueprint"
        )
        return
    # Create the parameter
    logger.debug(
        f"Creating Parameter: {new_param_name}, type: {type}, " f"label: {param_label}"
    )
    cf, cf_created = create_cf(
        new_param_name,
        param_label,
        description,
        cf_type,
        allow_multiple,
        required,
        show_on_servers=True,
    )

    if is_aws_param:
        cf.orchestration_hooks.add(aws_params_hook)
        env_field = CustomField.objects.get(name="cft_env_id")
        logger.debug(f"Env Field name {env_field.name}")
        dep = create_field_dependency(env_field, cf)
        logger.debug(f"Field Dep {dep.id}, {dep}")
        cf.save()

    # Add it to the Blueprint
    blueprint.custom_fields_for_resource.add(cf)

    add_param_values(blueprint, cf, cf_type, template_params, key, cf_created)

    if cf_created:
        # On the first time the param is created want to add the param to
        # param display sequence. When first creating the blueprint this will
        # ensure that the params show in the BP in the order they are listed
        # in the CFT. Later added params will need to be manually moved in the
        # display sequence
        SequencedItem.objects.get_or_create(custom_field=cf)


def check_aws_param(param_key, template_content, template_params):
    # Will look for params matching values in the list and add gen options
    valid_aws_params = [
        "AWS::EC2::Subnet::Id",
        "AWS::EC2::Image::Id",
        "AWS::EC2::AvailabilityZone::Name",
        "AWS::EC2::KeyPair::KeyName",
    ]
    param_type = template_params[param_key]["Type"]
    if param_type in valid_aws_params:
        return True, param_type
    valid_non_aws_params = [
        "InstanceType",
    ]
    resources = template_content["Resources"]
    for key in resources.keys():
        resource = resources[key]
        if resource["Type"] == "AWS::EC2::Instance":
            for value in valid_non_aws_params:
                resource_instance_type = resource["Properties"].get(value)
                if resource_instance_type:
                    ref = resource_instance_type.get("Ref")
                    if ref == param_key:
                        return True, value
    return False, param_type


def add_param_values(blueprint, cf, cf_type, template_params, key,
                     cf_created: False):
    # Create Add value from parameters file as selection in dropdown
    # If a list of allowed values exists, then we want to create a dropdown
    # with those values. If allowed values are not set, check the template
    # for a default value to set a single value for the parameter options
    # Also check constraints for the parameter and add if applicable
    param = template_params[key]
    allowed_values = param.get("AllowedValues", None)
    if allowed_values:
        add_cfvs_for_field(blueprint, cf, cf_type, allowed_values)
    else:
        if cf_created:
            # Only want to create default value on the first import. After that
            # This should be controlled by the parameter in CB.
            default_value = param.get("Default", None)
            if default_value:
                add_cfvs_for_field(blueprint, cf, cf_type, [default_value])

    check_and_set_constraints(param, cf)


def check_and_set_constraints(param, cf):
    # See if any valid constraints exist on the Parameter. Currently supported:
    # MinValue, MinLength, MaxValue, MaxLength, AllowedPattern
    constraints = {}
    try:
        constraints["minimum"] = Decimal(param["MinValue"])
    except KeyError:
        try:
            constraints["minimum"] = Decimal(param["MinLength"])
        except KeyError:
            pass
        pass
    try:
        constraints["maximum"] = Decimal(param["MaxValue"])
    except KeyError:
        try:
            constraints["maximum"] = Decimal(param["MaxLength"])
        except KeyError:
            pass
        pass
    try:
        constraints["regex_constraint"] = param["AllowedPattern"]
    except KeyError:
        pass
    if constraints:
        set_constraints_for_cf(cf, constraints)


def create_resource_type(type_name, **kwargs):
    defaults = {}
    for key, value in kwargs.items():
        defaults[key] = value
    resource_type, _ = ResourceType.objects.get_or_create(
        name=type_name, defaults=defaults
    )
    return resource_type


def set_constraints_for_cf(cf, constraints: dict):
    """
    Set Global Constraints for a Custom Field.
    Constraints dictionary can include the following fields:
    - minimum
    - maximum
    - slider_increment
    - regex_constraint
    """
    cfm = CustomFieldMapping.global_constraints.filter(custom_field=cf).first()
    if cfm is None:
        try:
            cfm, __ = CustomFieldMapping.global_mappings_not_defaults.get_or_create(
                custom_field=cf
            )
        except AttributeError:
            # Prior to 9.4.7 the above method did not exist. Will create a new
            # global constraint
            cfm, __ = CustomFieldMapping.global_constraints.get_or_create(
                custom_field=cf
            )
    cf.set_constraints_in_cfm(cfm, constraints)


def get_high_level_parameters(resource):
    stack_name = resource.get_cfv_for_custom_field("cft_stack_name").value
    try:
        cft_url = resource.get_cfv_for_custom_field("cft_url").value
        conn_info_id = resource.get_cfv_for_custom_field("cft_conn_info_id").value
        cft = get_cft_from_source(conn_info_id, cft_url)
        logger.info(
            f"Successfully connected to source control to get latest "
            f"version of Cloud Formation Template"
        )
    except:
        logger.warn(
            f"Unable to connect to source control to get latest "
            f"version. Using original CFT."
        )
        cft = resource.get_cfv_for_custom_field("cloud_formation_template").value
    return cft, stack_name


def fetch_parameters_for_cft_deployment(resource, cft_prefix):
    """
    Get & return the parameters to pass to the AWS job to deploy the CF
    template.
    These are the parameters that are defined in the CF template itself as
    inputs for the CF template deployment.
    Look for these parameters on the Resource object, where the deploy BP job
    in CB created them.
    """
    parameters = []
    cfvs = resource.get_cf_values_as_dict()
    for key in cfvs.keys():
        if key.find(cft_prefix) == 0:
            param_key = key.replace(cft_prefix, "")
            param_key = replace_cft_types_in_key(param_key)
            value = cfvs[key]
            parameters.append({"ParameterKey": param_key, "ParameterValue": value})
            cf = CustomField.objects.get(name=key)
            if cf.type == "PWD":
                logger.debug(f"Setting password: {param_key} to: ******")
            else:
                logger.debug(f"Setting param: {param_key} to: {value}")
    return parameters


def replace_cft_types_in_key(param_key):
    # If an AWS param, the end has the type postfixed, split at __AWS_, then
    # return just the parameter name without type
    param_key = param_key.split("__AWS_")[0]
    return param_key


def submit_template_request(stack_name, cft, parameters, resource, client):
    """
    return stack dict if successful; raises exception on failure
    """
    timeout = 900
    logger.debug(
        f"Submitting request for CloudFormation template. stack_name:"
        f" {stack_name} template: {cft}"
    )
    set_progress(
        f"Submitting CloudFormation request to AWS. This can take a "
        f"while. Timeout is set to: {timeout}"
    )

    try:
        set_progress(f'Creating stack "{stack_name}"')
        capabilities = resource.cft_capabilities
        if not capabilities:
            capabilities = []
        response = client.create_stack(
            StackName=stack_name,
            TemplateBody=cft,
            Parameters=parameters,
            TimeoutInMinutes=timeout,
            OnFailure=resource.cft_fail_behavior,
            Capabilities=capabilities,
        )
        stack_id = response["StackId"]
        set_progress(f'Created StackId: {stack_id}')
        stack = wait_for_stack_completion(client, stack_id)
        return stack
    except Exception as err:
        set_progress("Stack creation was not successful")
        raise err


def wait_for_stack_completion(client, stack_name):
    response = client.describe_stacks(StackName=stack_name)
    stack = response["Stacks"][0]
    # wait for stack to be created. Check status every minutes
    while stack["StackStatus"] == "CREATE_IN_PROGRESS":
        set_progress(f'status of {stack_name}: "{stack["StackStatus"]}"')
        time.sleep(15)
        response = client.describe_stacks(StackName=stack_name)
        stack = response["Stacks"][0]

    if stack["StackStatus"] == "CREATE_COMPLETE":
        set_progress("Stack creation was successful")
        return stack
    else:
        events = client.describe_stack_events(StackName=stack_name)
        logger.debug(events)
        error_msg = ""
        i = 1
        for event in events["StackEvents"]:
            if event["ResourceStatus"] == "CREATE_FAILED":
                error_msg += f'Error {i}: {event["ResourceStatusReason"]} '
                i += 1
        raise Exception(error_msg)


def update_cb_resource(resource, stack, env, job, client, cft_prefix):
    """
    Sets metadata on the resource object in the CB DB, then create/update
    server records within that resource
    """
    logger.debug(f"Stack info: {stack}")
    # Write outputs to Resource
    write_outputs_to_resource(resource, stack, cft_prefix)
    write_stack_id_to_resource(resource, stack)
    # Associate Servers (EC2 Instances) with Resource
    create_or_update_cb_servers(resource, env, job, client)


def write_stack_id_to_resource(resource, stack):
    stack_id = stack["StackId"]
    field_name = "cft_stack_id"
    create_cf(field_name, "CloudFormation Stack ID",
              "ID of the provisioned CloudFormation Stack",
              show_on_servers=True, show_as_attribute=True)
    resource.set_value_for_custom_field(field_name, stack_id)


def write_outputs_to_resource(resource, stack, cft_prefix):
    """
    Write the outputs of the executed CFT back to the Resource as Parameters
    """
    try:
        outputs = stack["Outputs"]
    except KeyError:
        logger.debug("No outputs defined for CFT, skipping outputs.")
        return
    for output in outputs:
        try:
            description = output["Description"]
        except KeyError:
            description = "Used by the CloudFormation Template blueprint"

        label = output["OutputKey"]
        field_name = f"{cft_prefix}output_{label}"
        value = output["OutputValue"]
        logger.debug(f"Writing output to Resource. Label: {label}, value: " f"{value}")
        create_field_set_value(field_name, label, value, description, resource)


def create_or_update_cb_servers(resource, env, job, client):
    cft_resources = client.describe_stack_resources(StackName=resource.cft_stack_name)
    group = resource.group
    rh = env.resource_handler.cast()
    for cft_resource in cft_resources["StackResources"]:
        if cft_resource["ResourceType"] == "AWS::EC2::Instance":
            svr_id = cft_resource["PhysicalResourceId"]
            if svr_id:
                ec2_client = rh.get_boto3_client(
                    region_name=env.aws_region, service_name="ec2"
                )
                ec2_data = ec2_client.describe_instances(InstanceIds=[svr_id])[
                    "Reservations"
                ][0]["Instances"][0]
                az = ec2_data["Placement"]["AvailabilityZone"]
                region = az[:-1]
                vpc_id = ec2_data["VpcId"]
                instance_type = ec2_data["InstanceType"]
                try:
                    server = Server.objects.get_or_create(
                        resource_handler_svr_id=svr_id,
                        group=group,
                        environment=env,
                        resource_handler=rh,
                    )[0]
                    server.resource = resource
                    server.owner = resource.owner
                    server.save()

                    tech_dict = {
                        "ec2_region": region,
                        "availability_zone": az,
                        "instance_id": svr_id,
                        "vpc_id": vpc_id,
                        "instance_type": instance_type,
                    }
                    rh.update_tech_specific_server_details(server, tech_dict)
                    try:
                        server.refresh_info()
                    except NotFoundException:
                        logger.warning(
                            f"Server object could not be created for "
                            f"instance id: svr_id, check to be sure "
                            f"that the VPC selected is available in "
                            f"CloudBolt"
                        )
                except Exception as err:
                    set_progress(
                        f"Adding a server to the resource failed. " f"error: {err}"
                    )
                    raise err

                # Add server to the job.server_set, and set creation event
                job.server_set.add(server)
                job.save()
                msg = "Server created by CloudFormation Template job"
                add_server_event("CREATION", server, msg, profile=job.owner, job=job)


def save_cft_to_resource(resource, cft):
    cf = create_cf(
        "cloud_formation_template",
        "CloudFormation Template",
        "CloudFormation Template Contents",
        cf_type="CODE",
    )
    resource.set_value_for_custom_field("cloud_formation_template", cft)


def create_field_set_value(field_name, label, value, description, resource):
    create_cf(field_name, label, description, show_on_servers=True)
    resource.set_value_for_custom_field(field_name, value)
    return resource


def render_parameters(resource, environment, job):
    # Go through all parameters on the resource and render them with Django
    # Templating
    params = resource.get_cf_values_as_dict()
    context = {
        "resource": resource,
        "environment": environment,
        "group": resource.group,
        "job": job,
    }
    context = Context(context)
    for key in params.keys():
        value = params[key]
        if type(value) == str:
            if value.find('{{') > -1 or value.find('{%') > -1:
                template = Template(value)
                # Hit some instances where strings were rendering with unicode hex
                # html.unescape fixes this
                rendered_value = html.unescape(template.render(context))
                if rendered_value != value:
                    logger.info(f'Rendered value: {value} to '
                                f'rendered_value: {rendered_value}')
                    resource.set_value_for_custom_field(key, rendered_value)
    return resource
