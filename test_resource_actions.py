import json
import logging

import pytest

from cmp.api.request_objects.actions.actions import ExportResourceActions
from cmp.api.tests.actions.actions_base import BaseActionsTest
from cmp.api.tests.base_api_test import BaseApiTest
from content.content_library.content_types import ContentTypes
from cmp.api.request_objects.utils import get_resource_info_by_order_id
from common.api.helpers import (
    get_global_id_from_response,
    get_href,
)
from cmp.api.request_objects.actions.actions import RunResourceActions


class TestResourceActionEndPointsAsAdmin(BaseActionsTest):
    def setup_class(self):
        super().setup_class()
        self.import_filepath = ContentTypes.resource_action.file_path[:-1]
        self.outer_zip_dir = "testytest"
        self.inner_zip_actions = {"testytest.zip": ContentTypes.orchestration_hook}
        self.content_type = ContentTypes.resource_action
        self.request_obj = ContentTypes.resource_action.endpoint
        self.export_obj = ExportResourceActions

    @pytest.mark.parametrize("version", ["v3"])
    def test_resource_action_import_export(self, version):
        logging.info(f"Importing {version} .zip archive")
        self.import_filename = f"{self.import_filepath}_{version}.zip"
        logging.info("***** Testing importing resource action *****")
        import_resp = self.check_action_import()

        logging.info("***** Testing inner zip base action import *****")
        self.check_inner_zip_action(import_resp)

        logging.info(
            "***** Testing exporting resource action with instance-specific info *****"
        )
        self.check_instance_specific_info(import_resp["id"])

        # logging.info("***** Testing importing resource action with replace existing *****")
        # self.check_replace_existing

    def test_run_resource_action_with_parameter(self, aws_ecs_orders):
        client = BaseApiTest()
        logging.info("**** Testing Run resource Action *****")
        order_results = aws_ecs_orders()
        order_id = list(order_results.keys())[0]
        resource_json = get_resource_info_by_order_id(order_id)
        resource_href = get_href(resource_json, "self")
        resource_id = resource_json["id"]
        testytest_action_id = get_global_id_from_response(
            resource_json, object_type="actions", title="testytest"
        )
        request_body = {"resource": f"/api/v3/cmp/resources/{resource_id}",
                        "parameters": {
                            "test_param": "Parameter Validation Test"
                        }
                        }
        resource_action = RunResourceActions(testytest_action_id, resource_href)
        logging.info(f"Running resource with parameters {resource_id}...")
        client.create_obj_validate_resp(
            request_data=resource_action,
            body=json.dumps(request_body),
            status_code_expected=200,
        )