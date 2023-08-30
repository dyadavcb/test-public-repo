"""
Handles generating options for AWS Specific Parameters in a CloudFormation template
https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/parameters-section-structure.html#aws-specific-parameter-types

Most of the data from these lookups are available in the AWS Resource Handler
"""
from accounts.models import Group
from common.methods import set_progress
from infrastructure.models import Environment
from servicecatalog.models import ServiceBlueprint
from utilities.logger import ThreadLogger

logger = ThreadLogger("GenerateOptionsCFT")


def get_az_options(environment, allowed_values):
    try:
        opts = environment.get_cfvs_for_custom_field('aws_availability_zone')
        options = get_options(opts, allowed_values)
        return options
    except Exception as err:
        return [("", f"--- Error encountered in Plugin: {err} ---")]


def get_image_options(environment, allowed_values):
    try:
        images = environment.os_builds.all()
        rh = environment.resource_handler
        options = []
        for image in images:
            try:
                osba = image.osba_for_resource_handler(rh, environment)
                value = osba.cast().ami_id
            except Exception as err:
                logger.warn(f'Unable to get AMI ID for OS Build: '
                            f'{image.name}. Error: {err}')
                continue
            if allowed_values:
                if value not in allowed_values:
                    # We cannot submit a value that isn't also present in the CFT
                    # as an allowed value, the execution would fail
                    continue
            label = image.name
            options.append((value, label))
        if not options:
            options = [("", f"--- No valid options found on Environment ---")]
        return options
    except Exception as err:
        return [("", f"--- Error encountered in Plugin: {err} ---")]


def get_subnet_options(environment, group, allowed_values):
    try:
        networks = environment.networks().keys()
        options = []
        for network in networks:
            net_groups = network.get_groups()
            if net_groups:
                if group not in net_groups:
                    # Only want to display Networks that group has perms on
                    continue
            value = network.network
            if allowed_values:
                if value not in allowed_values:
                    # We cannot submit a value that isn't also present in the CFT
                    # as an allowed value, the execution would fail
                    continue
            # Add the Subnet in to the Label for easy identification if it
            # isn't already included
            net_az = network.awsvpcsubnet.availability_zone
            label = network.name
            if label.find(net_az) == -1:
                label = f'{label}-{net_az}'
            options.append((value, label))
        if not options:
            options = [("", f"--- No valid options found on Environment ---")]
        return options
    except Exception as err:
        return [("", f"--- Error encountered in Plugin: {err} ---")]


def get_instance_type_options(environment, allowed_values):
    try:
        opts = environment.get_cfvs_for_custom_field('instance_type')
        options = get_options(opts, allowed_values)
        return options
    except Exception as err:
        return [("", f"--- Error encountered in Plugin: {err} ---")]


def get_keypair_options(environment, allowed_values):
    try:
        opts = environment.get_cfvs_for_custom_field('key_name')
        options = get_options(opts, allowed_values)
        return options
    except Exception as err:
        return [("", f"--- Error encountered in Plugin: {err} ---")]


def get_options(opts, allowed_values):
    options = []
    for opt in opts:
        value = opt.value
        if allowed_values:
            if value not in allowed_values:
                continue
        options.append((value, value))
    if not options:
        options = [("", f"--- No valid options found on Environment ---")]
    return options


def get_group_from_kwargs(kwargs):
    group = kwargs.get("group")
    if not group:
        form_dict = kwargs.get("form_data")
        group_id = int(form_dict.get("order_group")[0])
        group = Group.objects.get(id=group_id)
        if not group:
            raise Exception("Group could not be determined from form")
    return group


def get_bp_from_kwargs(kwargs):
    bp = kwargs.get("blueprint")
    if not bp:
        form_dict = kwargs.get("form_data")
        bp_url = form_dict.get("form-action")[0]
        bp_id = int(bp_url.split('/catalog/')[1].split('/')[0])
        bp = ServiceBlueprint.objects.get(id=bp_id)
        if not bp:
            raise Exception("BP could not be determined from form")
    return bp


def get_allowed_values(field, bp):
    cfvs = bp.get_cfvs_for_custom_field(field.name)
    logger.debug(f'allowed cfvs: {cfvs}')
    allowed_values = []
    for cfv in cfvs:
        allowed_values.append(cfv.value)
    logger.debug(f'allowed values: {allowed_values}')
    return allowed_values


def get_options_list(field, control_value=None, **kwargs):
    if not control_value:
        options = [("", "--- First, Select an Environment ---")]
    else:
        options = []
        field_data = field.name.split("_")
        aws_param_service = field_data[-3]
        aws_param_ns = field_data[-2]
        aws_param_name = field_data[-1]
        environment = Environment.objects.get(id=control_value)
        bp = get_bp_from_kwargs(kwargs)
        allowed_values = get_allowed_values(field, bp)
        if aws_param_service == "EC2":
            if aws_param_ns == "AvailabilityZone":
                options = get_az_options(environment, allowed_values)
            if aws_param_ns == "Image":
                options = get_image_options(environment, allowed_values)
            if aws_param_ns == "Subnet":
                group = get_group_from_kwargs(kwargs)
                options = get_subnet_options(environment, group,
                                             allowed_values)
            if aws_param_ns == "KeyPair":
                options = get_keypair_options(environment, allowed_values)
            if aws_param_ns == "InstanceType":
                options = get_instance_type_options(environment,
                                                    allowed_values)
    # TODO add handling for other AWS Param types
    return options
