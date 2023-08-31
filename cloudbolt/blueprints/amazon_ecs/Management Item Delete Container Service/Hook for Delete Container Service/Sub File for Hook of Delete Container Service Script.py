from infrastructure.models import Environment, CustomField
from resources.models import Resource, ResourceType
from servicecatalog.models import ServiceBlueprint


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


def generate_options_for_container_service(resource, **kwargs):
    if resource is None:
        return []
    
    return [(sub_resource.id, sub_resource.name) for sub_resource in Resource.objects.filter(parent_resource=resource, lifecycle="ACTIVE")]



def run(resource, *args, **kwargs):
    container_service = Resource.objects.get(id="{{ container_service }}")
    
    # get enviroment model object
    env_obj = Environment.objects.get(id=resource.env_id)
    
    # get aws ecs boto3 client
    client = _boto3_ecs_client(env_obj)
    
    try:
        # create container service 
        service_rsp = client.delete_service(
                                cluster=resource.cluster_name,
                                service=container_service.name,
                                force=True
                            )
    except Exception as error:
        return "FAILURE", "", f"{error}"
    
    container_service.delete()
        
    return "SUCCESS", "Container service deleted successfully", ""