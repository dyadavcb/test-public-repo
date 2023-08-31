from infrastructure.models import Environment, CustomField
from resources.models import Resource, ResourceType

def _get_or_create_custom_fields():

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
        name='network_mode', defaults={ "label": 'Network Mode', "type": 'STR', 'show_on_servers':True }
    )
    CustomField.objects.get_or_create(
        name='task_definition', defaults={ "label": 'Task Definition', "type": 'STR', 'show_on_servers':True}
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


def run(resource, *args, **kwargs):
    # get or create custom fields if needed
    _get_or_create_custom_fields()
    
    environment = Environment.objects.get(id=resource.env_id)
    ecs = _boto3_ecs_client(environment)
    
    # get or create sub resource type
    resource_type, _ = ResourceType.objects.get_or_create(
            name="container_service", defaults={"label": "Container Service", "icon": "far fa-file"})
    
    for service_arn in ecs.list_services(cluster=resource.cluster_name)['serviceArns']:
        
        service_name = service_arn.split('/')[2]
        
        # get cluster service object
        service_resource = Resource.objects.filter(name=service_name, blueprint=resource.blueprint, parent_resource=resource).first()
        
        if service_resource is None:
            # create cluster service
            service_resource = Resource.objects.create(
                                    name=service_name,
                                    blueprint= resource.blueprint,
                                    resource_type= resource_type,
                                    parent_resource= resource,
                                    group = resource.group
                                )
        
        # get cluster services
        service_rsp = ecs.describe_services(cluster=resource.cluster_name, services=[service_name])['services']
        
        if not service_rsp:
            continue
        
        service_resource.desired_count = service_rsp[0].get('desiredCount')
        service_resource.running_count = service_rsp[0].get('runningCount')
        service_resource.status = service_rsp[0].get('status')
        service_resource.launch_type = service_rsp[0].get('launchType')
        
        # get task difinition object
        taskDefinition = ecs.describe_task_definition(taskDefinition=service_rsp[0]['taskDefinition']).get("taskDefinition", None)
        
        if taskDefinition is not None:
            service_resource.network_mode = taskDefinition.get('networkMode', '')
            service_resource.task_definition = taskDefinition['containerDefinitions'][0]['name']

        service_resource.lifecycle = 'ACTIVE'
        service_resource.save()
             
        
    return "SUCCESS", "Container service synced successfully", ""