"""
Build service item action for AWS EC2 Cluster Service blueprint.
"""
from common.methods import set_progress
from accounts.models import Group
from infrastructure.models import CustomField, Environment


def sort_dropdown_options(data, placeholder=None, is_reverse=False):
    """
    Sort dropdown options 
    """
    # remove duplicate option from list
    data = list(set(data))

    # sort options
    sorted_options = sorted(data, key=lambda tup: tup[1].lower(), reverse=is_reverse)
    
    if placeholder is not None:
        sorted_options.insert(0, placeholder)
    
    return {'options': sorted_options, 'override': True}
    
    
def generate_options_for_env_id(**kwargs):
    """
    Generate AWS region options
    """
    group_name = kwargs["group"]

    try:
        group = Group.objects.get(name=group_name)
    except Exception as err:
        return []
    
    # fetch all group environment
    envs = group.get_available_environments()
    
    options = [(env.id, env.name) for env in envs if env.resource_handler is not None and env.resource_handler.resource_technology.slug.startswith('aws')]
    
    return sort_dropdown_options(options, ("", "-----Select Environment-----"))

def get_boto3_service_client(env, service_name="ecs"):
    """
    Return boto connection to the ECS in the specified environment's region.
    """
    # get aws resource handler object
    rh = env.resource_handler.cast()

    # get aws wrapper object
    wrapper = rh.get_api_wrapper()

    # get aws client object
    client = wrapper.get_boto3_client(service_name, rh.serviceaccount, rh.servicepasswd, env.aws_region)
    
    return client


def create_custom_fields():
    CustomField.objects.get_or_create(
        name='aws_rh_id',
        defaults={
            "label": 'AWS EC2 Cluster Service RH ID',
            "type": 'STR',
        }
    )
    CustomField.objects.get_or_create(
        name='aws_region',
        defaults={
            "label": 'AWS EC2 Cluster Service RH ID',
            "type": 'STR',
        }
    )
    CustomField.objects.get_or_create(
        name='env_id',
        defaults={
            "label": 'AWS EC2 Cluster Service Environment ID',
            "type": 'STR',
        }
    )
    CustomField.objects.get_or_create(
        name='cluster_name',
        defaults={
            "label": 'AWS cluster name',
            "type": 'STR',
        }
    )


def run(resource, logger=None, **kwargs):
    set_progress("Starting Provision of the AWS ECS Cluster...")
    
    # get or create custom field if needed
    create_custom_fields()
    
    env = Environment.objects.get(id='{{ env_id }}')
    cluster_name = '{{ cluster_name }}'

    set_progress('Connecting to Amazon ECS Cluster Service')
    ecs = get_boto3_service_client(env)
    
    set_progress('Create ECS Cluster Service cluster "{}"'.format(cluster_name))

    try:
        ecs.create_cluster(
            clusterName=cluster_name
        )
    except Exception as err:
        return "FAILURE", "", err
    
    resource.name = cluster_name
    resource.cluster_name = cluster_name
    resource.aws_region = env.aws_region
    resource.aws_rh_id = env.resource_handler.cast().id
    resource.env_id = env.id
    resource.save()
    
    return "SUCCESS", "ECS cluster created successfully", ""