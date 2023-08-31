from infrastructure.models import Environment


def _boto3_ecs_client(env_obj):
    """
    Return boto connection to the ecs in the specified environment's region.
    """
    rh = env_obj.resource_handler.cast()
    
    wrapper = rh.get_api_wrapper()
    
    client = wrapper.get_boto3_client(
        'ecs',
        rh.serviceaccount,
        rh.servicepasswd,
        env_obj.aws_region
    )
    return client


def generate_options_for_task_definition(resource, **kwargs):
    if resource is None:
        return []
        
    env_obj = Environment.objects.get(id=resource.env_id)
    
    client = _boto3_ecs_client(env_obj)
    
    result = client.list_task_definitions(status='ACTIVE')

    task_definitions = result.get('taskDefinitionArns')
    
    return [task_definition.split('/')[-1] for task_definition in task_definitions]


def run(resource, *args, **kwargs):
    
    taskDefinition = "{{ task_definition }}"
    
    env_obj = Environment.objects.get(id=resource.env_id)

    client = _boto3_ecs_client(env_obj)

    try:
        client.deregister_task_definition(
            taskDefinition=taskDefinition,
        )
    except Exception as error:
        return "FAILURE", "", f"{error}"

    return "SUCCESS", "Task Definition deregister successfully", ""