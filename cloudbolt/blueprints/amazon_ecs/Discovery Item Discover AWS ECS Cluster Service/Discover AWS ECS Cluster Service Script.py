"""
Build service item action for AWS ECS Cluster Service blueprint.
"""
from common.methods import set_progress
from infrastructure.models import Environment, CustomField
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


RESOURCE_IDENTIFIER = ['cluster_name', 'aws_region']

def _boto3_ecs_client(env_obj):
    """
    Return boto connection to the ecs in the specified environment's region.
    """
    rh = env_obj.resource_handler.cast()
    
    wrapper = rh.get_api_wrapper()
    
    if env_obj.aws_region:
        client = wrapper.get_boto3_client(
            'ecs',
            rh.serviceaccount,
            rh.servicepasswd,
            env_obj.aws_region
        )
        return client
        
    return None

def _get_or_create_custom_fields():
    CustomField.objects.get_or_create(
        name='cluster_name', defaults={ "label": 'Cluster Name', "type": 'STR' }
    )
    CustomField.objects.get_or_create(
        name='aws_region', defaults={ "label": 'Region', "type": 'STR' }
    )    
    CustomField.objects.get_or_create(
        name='env_id', defaults={ "label": 'Environment ID', "type": 'STR' }
    )
    
    CustomField.objects.get_or_create(
        name='aws_rh_id', defaults={ "label": 'Resource Handler ID', "type": 'STR' }
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
        name='network_mode', defaults={ "label": 'Network Mode', "type": 'STR', 'show_on_servers':True }
    )
    CustomField.objects.get_or_create(
        name='task_definition', defaults={ "label": 'Task Definition', "type": 'STR', 'show_on_servers':True}
    )
    

def discover_resources(**kwargs):
    # create custom feilds if needed 
    _get_or_create_custom_fields()
    
    # get all aws environments
    environments = Environment.objects.filter(resource_handler__resource_technology__name="Amazon Web Services")
    discovered = []
    verify_discovered = [] 
    
    for environment in environments:
        ecs = _boto3_ecs_client(environment)
        
        if ecs is None:
            continue
            
        handler = environment.resource_handler.cast()

        try:
            clusterArns = ecs.list_clusters()['clusterArns']
        except Exception as err:
            raise Exception(err)
        
        
        for cluster_arn in clusterArns:
            
            cluster_name = cluster_arn.split('/')[1]
            idtf_resource_name = f"{cluster_name}:{environment.aws_region}"
            
            if idtf_resource_name in verify_discovered:
                continue
            
            verify_discovered.append(idtf_resource_name)
            
            discovered.append({
                "name": cluster_name,
                "env_id": environment.id,
                "aws_region": environment.aws_region,
                "aws_rh_id":  handler.id,
                "cluster_name":cluster_name
            })
            
            
            logger.info(f"Finished syncing Cluster : {cluster_name}")
            
    return discovered