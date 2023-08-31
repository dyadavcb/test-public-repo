from infrastructure.models import Environment, CustomField

def _get_or_create_custom_fields():
    CustomField.objects.get_or_create(
        name='network_mode', defaults={ "label": 'Network Modee', "type": 'STR' }
    )
    CustomField.objects.get_or_create(
        name='task_definition', defaults={ "label": 'Task Definition', "type": 'STR', 'show_on_servers':True }
    )    
    CustomField.objects.get_or_create(
        name='aws_subnet', defaults={ "label": 'Subnet', "type": 'STR' }
    )
    
    CustomField.objects.get_or_create(
        name='aws_security_group', defaults={ "label": 'Security Group', "type": 'STR' }
    ) 
    CustomField.objects.get_or_create(
        name='launch_type', defaults={ "label": 'Launch Type', "type": 'STR' }
    )
   
   
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

def generate_options_for_task_definition(resource, **kwargs):
    if resource is None or resource =="":
        return []
    
    # get enviroment model object
    env_obj = Environment.objects.get(id=resource.env_id)
    
    # get aws ecs boto3 client
    client = _boto3_ecs_client(env_obj)
    
    # fetch all task definition families
    resp = client.list_task_definitions(status='ACTIVE')
    
    task_definitions = resp.get('taskDefinitionArns')
    
    return [("{0}???{1}".format(xx.split('/')[-1], resource.env_id), xx.split('/')[-1]) for xx in task_definitions]

def generate_options_for_launch_type(resource, control_value=None, **kwargs):
    if control_value is None or control_value =="" :
        return []
    
    control_value = control_value.split("???")
    
    # get enviroment model object
    env_obj = Environment.objects.get(id=control_value[1])
    
    # get aws ecs boto3 client
    client = _boto3_ecs_client(env_obj)
    
    requiresCompatibilities = client.describe_task_definition(taskDefinition=control_value[0])['taskDefinition']['requiresCompatibilities']
    
    if "FARGATE" not in requiresCompatibilities:
        return []
    
    return ["FARGATE"]
    

def generate_options_for_network_mode(resource, control_value=None, **kwargs):
    if control_value is None or control_value =="":
        return []
        
    control_value = control_value.split("???")
    
    # get enviroment model object
    env_obj = Environment.objects.get(id=control_value[1])
    
    # get aws ecs boto3 client
    client = _boto3_ecs_client(env_obj)
    
    return [client.describe_task_definition(taskDefinition=control_value[0])['taskDefinition']['networkMode']]

def generate_options_for_aws_subnet(resource, **kwargs):
    if resource is None or resource =="":
        return []
        
    # fetch enviroment model object
    env_obj = Environment.objects.get(id=resource.env_id)
    
    return [env_obj.sc_nic_0.network]
    
def generate_options_for_aws_security_group(resource, **kwargs):
    if resource is None or resource =="":
        return []
    
    # fetch enviroment model object
    env_obj = Environment.objects.get(id=resource.env_id)
    
    ec2_client = _boto3_ecs_client(env_obj, "ec2")

    scy_all = ec2_client.describe_security_groups()
    
    return [(scy['GroupId'], scy['GroupName']) for scy in scy_all['SecurityGroups'] if 'VpcId' in scy and scy['VpcId'] == env_obj.vpc_id]
    
def run(resource, *args, **kwargs):

    task_definition = "{{ task_definition }}".split("???")[0]
    launchType = "{{ launch_type }}"
    network_mode = "{{ network_mode }}"

    
    env_obj = Environment.objects.get(id=resource.env_id)
    client = _boto3_ecs_client(env_obj)

    task_kwargs = {'cluster': resource.cluster_name, 'taskDefinition': task_definition, 'launchType': launchType}
        
    if network_mode == "awsvpc":
        task_kwargs['networkConfiguration'] = {
            'awsvpcConfiguration': {
                'subnets': [
                    "{{aws_subnet}}",
                ],
                'securityGroups': [
                    "{{aws_security_group}}",
                ],
                'assignPublicIp': 'DISABLED'
            }
        }
        
    try:
        client.run_task(**task_kwargs)
    except Exception as error:
        return "FAILURE", "", f"{error}"

    return "SUCCESS", "Task Definition run successfully", ""