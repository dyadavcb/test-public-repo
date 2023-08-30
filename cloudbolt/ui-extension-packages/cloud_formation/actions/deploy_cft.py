"""
Build service item action for AWS CloudFormation(CF) Templates deployment.

This action was created by the AWS CloudFormation UI Extension.

Do not edit this script directly as all resources provisioned by the AWS
CFT Builder Blueprint use this script. If you need to make one-off changes,
copy this script and create a new action leveraged by the blueprint that needs
the modifications.
"""
import time

from resources.models import Resource
from utilities.exceptions import NotFoundException

if __name__ == "__main__":
    import os
    import sys
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
    sys.path.append("/opt/cloudbolt")
    sys.path.append("/var/opt/cloudbolt/proserv")
    django.setup()

from jobs.models import Job
from common.methods import set_progress
from infrastructure.models import Environment
from utilities.logger import ThreadLogger
from xui.cloud_formation.shared import get_high_level_parameters, \
    save_cft_to_resource, fetch_parameters_for_cft_deployment, \
    submit_template_request, update_cb_resource, render_parameters

logger = ThreadLogger(__name__)


def run(job, **kwargs):
    resource = kwargs.get("resource")
    if not resource:
        msg = f"CloudBolt Resource object not found."
        set_progress(msg)
        return "FAILURE", msg, ""
    set_progress(
        f"Starting deploy of CloudFormation Template for resource: " f"{resource}"
    )
    env = Environment.objects.get(id=resource.cft_env_id)
    rh = env.resource_handler.cast()
    resource = render_parameters(resource, env, job)
    cft, stack_name = get_high_level_parameters(resource)
    save_cft_to_resource(resource, cft)
    cft_prefix = f"cft_{resource.blueprint_id}_"
    parameters = fetch_parameters_for_cft_deployment(resource, cft_prefix)
    client = rh.get_boto3_client(
        region_name=env.aws_region, service_name="cloudformation"
    )
    try:
        stack = submit_template_request(stack_name, cft, parameters, resource, client)
        update_cb_resource(resource, stack, env, job, client, cft_prefix)
        return "SUCCESS", "CloudFormation Template deployment complete", ""
    except Exception as err:
        msg = f'CloudFormation Template deployment failed: {err}'
        return "FAILURE", "", msg


if __name__ == "__main__":
    job_id = sys.argv[1]
    j = Job.objects.get(id=job_id)
    run = run(j)
    if run[0] == "FAILURE":
        set_progress(run[1])
