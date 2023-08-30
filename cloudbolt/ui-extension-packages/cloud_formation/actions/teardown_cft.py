"""
Teardown Service Item Action for the CloudFormation Library

This action was created by the CloudFormation Library

Do not edit this script directly as all resources provisioned by the
CloudFormation Library use this script. If you need to make one-off
modifications, copy this script and create a new action leveraged by the
blueprint that needs the modifications.
"""
from resourcehandlers.aws.models import AWSHandler
from common.methods import set_progress
from utilities.logger import ThreadLogger
from infrastructure.models import Environment
import time

logger = ThreadLogger(__name__)


def get_ids(resource):
    try:
        cft_env_id = resource.cft_env_id
        if cft_env_id is None:
            raise Exception(f"Environment ID not found.")
    except:
        msg = "No Environment ID set on the blueprint, exiting"
        set_progress(msg)
        raise
    try:
        cft_stack_id = resource.cft_stack_id
        if cft_stack_id is None:
            raise Exception(f"CloudFormation Stack Name not found.")
    except:
        msg = "No CloudFormation Stack Name set on the blueprint, " "continuing"
        set_progress(msg)
        raise
    return cft_env_id, cft_stack_id


def run(job, *args, **kwargs):
    resource = job.resource_set.first()
    if resource:
        set_progress(f"Teardown CFT plugin running for resource: {resource}")
        try:
            cft_env_id, cft_stack_id = get_ids(resource)
        except Exception as err:
            msg = f"Environment or Stack ID could not be found, assuming " \
                  f"stack creation never completed. Exiting. Error: {err}"
            return "WARNING", msg, ""
        env = Environment.objects.get(id=cft_env_id)

        # Delete Resources
        try:
            delete_cft_stack(cft_stack_id, env)
        except Exception as err:
            set_progress("CloudFormation Stack deletion failed.")
            return "FAILURE", "", err

        # Set CB Server Records to HISTORICAL. This action has already deleted
        # the Virtual Machines themselves
        for server in resource.server_set.all():
            set_progress(f"Deleting CB Server record for: {server.hostname}")
            server.status = 'HISTORICAL'
            server.save()
        return "SUCCESS", "All resources successfully deleted", ""

    else:
        set_progress("Resource was not found")
        return "SUCCESS", "Resource was not found", ""


def delete_cft_stack(cft_stack_id, env):
    # Instantiate AWS Resource Client
    rh: AWSHandler = env.resource_handler.cast()
    wrapper = rh.get_api_wrapper()
    region = env.aws_region

    set_progress(
        f"Deleting AWS CloudFormation Stack with ID: " f"{cft_stack_id}"
    )
    client = wrapper.get_boto3_client(
        "cloudformation", rh.serviceaccount, rh.servicepasswd, region
    )
    client.delete_stack(StackName=cft_stack_id)
    wait_for_stack_deletion(client, cft_stack_id)


def wait_for_stack_deletion(client, stack_id):
    response = client.describe_stacks(StackName=stack_id)
    stack = response["Stacks"][0]
    # wait for stack to be created. Check status every minutes
    while stack["StackStatus"] == "DELETE_IN_PROGRESS":
        set_progress(f'status of {stack_id}: "{stack["StackStatus"]}"')
        time.sleep(15)
        response = client.describe_stacks(StackName=stack_id)
        stack = response["Stacks"][0]

    if stack["StackStatus"] == "DELETE_COMPLETE":
        set_progress("Stack deletion was successful")
        return stack
    else:
        events = client.describe_stack_events(StackName=stack_id)
        logger.debug(events)
        error_msg = ""
        i = 1
        for event in events["StackEvents"]:
            if event["ResourceStatus"] == "DELETE_FAILED":
                error_msg += f'Error {i}: {event["ResourceStatusReason"]} '
                i += 1
        set_progress(f'ERROR: {error_msg}')
        raise Exception(error_msg)

