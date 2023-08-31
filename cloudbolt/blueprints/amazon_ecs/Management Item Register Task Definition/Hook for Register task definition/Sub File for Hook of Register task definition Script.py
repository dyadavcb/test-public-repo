from infrastructure.models import Environment, CustomField
from resources.models import Resource, ResourceType
from common.methods import set_progress

def _boto3_ecs_client(env_obj, service_name="ecs"):
    """
    Return boto connection to the ecs in the specified environment's region.
    """
    rh = env_obj.resource_handler.cast()
    
    wrapper = rh.get_api_wrapper()
    
    client = wrapper.get_boto3_client(
        service_name,
        rh.serviceaccount,
        rh.servicepasswd,
        env_obj.aws_region
    )
    return client


def generate_options_for_launch_type_capabilities(**kwargs):
    # return ["FARGATE", "EC2", "EXTERNAL"]
    return ["FARGATE"]
    
def generate_options_for_networkMode(control_value=None, **kwargs):
    if control_value is None or control_value == "":
        return []
    
    if control_value == "FARGATE":
        return ["awsvpc"]
    elif control_value == "EXTERNAL":
        return ["none", "bridge", "host"]
        
    #return ["none", "bridge", "awsvpc", "host"]
    return ["bridge"]


def generate_options_for_protocol(**kwargs):
    return ["TCP", "UDP"]


def generate_options_for_essential(**kwargs):
    return [(True, "YES"), (False, "NO")]


def generate_options_for_create_service(**kwargs):
    return [(True, "YES"), (False, "NO")]

def generate_options_for_aws_subnet(resource, **kwargs):
    if resource is None or resource == "":
        return []
        
    # fetch enviroment model object
    env_obj = Environment.objects.get(id=resource.env_id)
    
    return [env_obj.sc_nic_0.network]
    
def generate_options_for_aws_security_group(resource, **kwargs):
    if resource is None or resource == "":
        return []
    
    # fetch enviroment model object
    env_obj = Environment.objects.get(id=resource.env_id)
    
    ec2_client = _boto3_ecs_client(env_obj, "ec2")

    scy_all = ec2_client.describe_security_groups()
    
    return [(scy['GroupId'], scy['GroupName']) for scy in scy_all['SecurityGroups'] if 'VpcId' in scy and scy['VpcId'] == env_obj.vpc_id]

def _get_or_create_custom_fields():
    CustomField.objects.get_or_create(
        name='family_name',  defaults={ "label": 'Family Name',  "type": 'STR', }
    )
    CustomField.objects.get_or_create(
        name='networkMode', defaults={ "label": 'Network Mode', "type": 'STR', 'show_on_servers':True }
    )
    CustomField.objects.get_or_create(
        name='create_service',  defaults={ "label": 'Create service', "type": 'BOOL', }
    )
    CustomField.objects.get_or_create(
        name='container_name', defaults={ "label": 'Container Name', "type": 'STR' }
    )
    
    CustomField.objects.get_or_create(
        name='docker_image', defaults={ "label": 'Docker Image', "type": 'STR' }
    )
    
    CustomField.objects.get_or_create(
        name='essential', defaults={ "label": 'Essential', "type": 'BOOL' }
    )
    
    CustomField.objects.get_or_create(
        name='memory', defaults={ "label": 'Memory', "type": 'INT' }
    )
    
    CustomField.objects.get_or_create(
        name='containerPort', defaults={ "label": 'Container Service Port', "type": 'INT' }
    )
    
    CustomField.objects.get_or_create(
        name='hostPort', defaults={ "label": 'Container Service Hostport', "type": 'INT' }
    )
    
    CustomField.objects.get_or_create(
        name='protocol', defaults={ "label": 'Container Service protocol', "type": 'INT' }
    )
    
    CustomField.objects.get_or_create(
        name='aws_subnet', defaults={ "label": 'Subnet', "type": 'STR' }
    )
    CustomField.objects.get_or_create(
        name='aws_security_group', defaults={ "label": 'Security Group', "type": 'STR' }
    )    
    CustomField.objects.get_or_create(
        name='status', defaults={ "label": 'Status', "type": 'STR' }
    )
    CustomField.objects.get_or_create(
        name='desired_count', defaults={ "label": 'Desired Count', "type": 'INT' }
    )
    CustomField.objects.get_or_create(
        name='running_count', defaults={ "label": 'Runnin Count', "type": 'INT' }
    )
    CustomField.objects.get_or_create(
        name='launch_type', defaults={ "label": 'Launch Type', "type": 'STR' }
    )
    CustomField.objects.get_or_create(
        name='task_definition', defaults={ "label": 'Task Definition', "type": 'STR', 'show_on_servers':True }
    )
    
def _create_container_service_for_cmp_instance(resource, service_rsp, networkMode, task_definition):
    # get or create sub resource type
    resource_type, _ = ResourceType.objects.get_or_create(
            name="container_service", defaults={"label": "Container Service", "icon": "far fa-file"})

    # create container service resource 
    ecs_resource, _ = Resource.objects.get_or_create(
        name=service_rsp.get('serviceName'),
        blueprint=resource.blueprint,
        parent_resource=resource,
        defaults={
            "group": resource.group,
            "resource_type": resource_type,
            "parent_resource": resource
        }
    )
    
    ecs_resource.desired_count = service_rsp.get('desiredCount')
    ecs_resource.running_count = service_rsp.get('runningCount')
    ecs_resource.status = service_rsp.get('status')
    ecs_resource.launch_type = service_rsp.get('launchType')
    ecs_resource.network_mode = networkMode
    ecs_resource.task_definition = task_definition
    ecs_resource.lifecycle = 'ACTIVE'
    ecs_resource.save()
    
def run(resource, *args, **kwargs):
    
    # get or create custom fields if needed
    _get_or_create_custom_fields()
    
    launch_type_capabilities = "{{launch_type_capabilities}}"
    
    family = "{{ family_name }}"
    networkMode = "{{ networkMode }}"
    create_service = False if "{{ create_service }}" == "False" else True

    containerDefinitions = {
            'name': "{{ container_name }}",
            'image': "{{ docker_image }}",
            'essential': bool("{{ essential }}"),
    }
    
    if networkMode != "none":
        containerPort = int("{{ containerPort }}")
        containerDefinitions['portMappings'] = [
                {
                    "containerPort": containerPort,
                    "hostPort": containerPort,
                    "protocol": "{{ protocol }}"
                }
            ]
    
    # fetch enviroment model object
    env_obj = Environment.objects.get(id=resource.env_id)
    
    # get boto3 ecs client
    client = _boto3_ecs_client(env_obj)
    
    task_definition_payload = {
        "family" : family,
        "networkMode": networkMode,
        "containerDefinitions" : [containerDefinitions],
        "requiresCompatibilities" : [launch_type_capabilities]
    }
    
    if launch_type_capabilities == "FARGATE":
        task_definition_payload['cpu'] = "0.5 vCPU"
        task_definition_payload['memory'] = "2048" #str(containerDefinitions['memory'])
        
        # container definition memory must be less than the task memory
        #del containerDefinitions['memory']
        
    set_progress(f"Register Task Definition Payload : {task_definition_payload}")
    
    try:
        task_rsp = client.register_task_definition(**task_definition_payload)
    except Exception as error:
        return "FAILURE", "", f"{error}"
    
    set_progress(f"Register Task Definition response : {task_rsp}")
    
    if create_service:
       
        service_kwargs = {'cluster': resource.cluster_name, 'serviceName': f"{family}Service", 'taskDefinition': family, 'desiredCount':1}
        
        if networkMode == "awsvpc":
            service_kwargs['networkConfiguration'] = {
                'awsvpcConfiguration': {
                    'subnets': [
                        "{{aws_subnet}}",
                    ],
                    'securityGroups': [
                        "{{aws_security_group}}",
                    ],
                    'assignPublicIp': 'DISABLED' if launch_type_capabilities == "FARGATE" else 'ENABLED'
                }
            }
        
        set_progress(f"Container Payload : {service_kwargs}")
        
        try:
            service_rsp = client.create_service(**service_kwargs)['service']
        except Exception as error:
            return "FAILURE", "", f"{error}"
        
        # create container service as a sub-resource on cmp instance for AWS ECS blueprint
        _create_container_service_for_cmp_instance(resource, service_rsp, networkMode, task_rsp.get("taskDefinition").get("taskDefinitionArn").split("/")[-1])
        
    return "SUCCESS", "Register task definition created successfully", ""