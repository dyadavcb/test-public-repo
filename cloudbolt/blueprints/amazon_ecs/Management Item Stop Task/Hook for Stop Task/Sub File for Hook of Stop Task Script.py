from infrastructure.models import Environment


   
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

def generate_options_for_tasks(resource, **kwargs):
    if resource is None or resource =="":
        return []

    # get enviroment model object
    env_obj = Environment.objects.get(id=resource.env_id)

    # get aws ecs boto3 client
    client = _boto3_ecs_client(env_obj)

    # fetch all tasks
    options =  [(task_obj, task_obj.split("/")[-1]) for task_obj in client.list_tasks(cluster=resource.name)['taskArns'] ]

    return options

    
def run(resource, *args, **kwargs):

    task_arn = "{{tasks}}"

    env_obj = Environment.objects.get(id=resource.env_id)
    client = _boto3_ecs_client(env_obj)

    try:
        client.stop_task(cluster=resource.name, task=task_arn)
    except Exception as error:
        return "FAILURE", "", f"{error}"

    return "SUCCESS", "Task stopped successfully", ""