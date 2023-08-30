from django import forms
from django.conf import settings
from django.core.files import File
from django.utils.translation import ugettext_lazy as _lazy

from common.forms import C2Form
from common.widgets import SelectizeMultiple
from resources.models import ResourceType
from servicecatalog.models import ServiceBlueprint
from utilities.forms import GitConnectionInfoForm
from utilities.models import ConnectionInfo

from xui.cloud_formation.shared import (
    generate_options_for_connection_info,
    generate_options_for_resource_type,
    generate_options_for_allowed_environments,
    create_resource_type,
    create_generated_options_action,
    get_cft_from_source,
    add_blueprint_label,
    create_blueprint_level_params,
    add_bp_items,
    create_params,
)

from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


class NewBlueprintForm(C2Form):
    def __init__(self, *args, **kwargs):
        super(NewBlueprintForm, self).__init__(*args, **kwargs)
        initial = kwargs.get("initial")
        if initial:
            initial_ci = initial.get("connection_info")
            initial_rt = initial.get("resource_type")
        else:
            initial_ci = None
            initial_rt = None
        ci_choices = generate_options_for_connection_info(initial_ci=initial_ci)
        env_choices = generate_options_for_allowed_environments()
        rt_choices = generate_options_for_resource_type(initial_rt=initial_rt)

        self.fields["name"] = forms.CharField(
            max_length=50,
            required=True,
            help_text="Name of the CloudBolt Blueprint",
        )
        self.fields["resource_type"] = forms.ChoiceField(
            label="Resource Type",
            choices=rt_choices,
            required=True,
            help_text="Select a Resource Type for the output resource"
        )
        self.fields["connection_info"] = forms.ChoiceField(
            label="Connection Info",
            choices=ci_choices,
            help_text="Connection Info for Github, Gitlab or Azure DevOps connection",
        )
        self.fields["cft_url"] = forms.CharField(
            label="CloudFormation Template URL",
            help_text="URL to raw git file",
            required=True
        )
        self.fields["allowed_environments"] = forms.MultipleChoiceField(
            choices=env_choices,
            widget=SelectizeMultiple,
            required=True,
            help_text="Select the environments where this blueprint should "
                      "have access"
        )

        for field, value in self.fields.items():
            value.required = True

        self.fields["id"] = forms.IntegerField(
            widget=forms.HiddenInput(), required=False
        )

    def clean(self):
        envs = self.cleaned_data.get("allowed_environments")

        if not envs:
            raise forms.ValidationError(
                {'allowed_environments': ['At least one environment must be '
                                          'selected.']}
            )

        if "all_capable" in envs:
            # Prevents other environments from being saved if all_capable is
            # selected
            self.cleaned_data["allowed_environments"] = ["all_capable"]

        return self.cleaned_data

    def save(self):
        id = self.cleaned_data.get("id")
        ci_id = self.cleaned_data.get("connection_info")
        name = self.cleaned_data.get("name")
        cft_url = self.cleaned_data.get("cft_url")
        allowed_envs = self.cleaned_data.get("allowed_environments")
        rt_id = self.cleaned_data.get("resource_type")

        template_json = get_cft_from_source(ci_id, cft_url)
        if id:
            blueprint = ServiceBlueprint.objects.get(id=id)
            blueprint.resource_type = ResourceType.objects.get(id=int(rt_id))
            blueprint.save()
        else:
            blueprint = ServiceBlueprint.objects.create(
                name=name, resource_type=ResourceType.objects.get(id=int(rt_id))
            )
            img = open(
                f"{settings.PROSERV_DIR}xui/cloud_formation/icons/cft_icon.png", "rb"
            )
            blueprint.list_image.save("new", File(img))
            img.close()

        create_blueprint_level_params(
            blueprint, template_json, cft_url, allowed_envs, ci_id
        )
        add_blueprint_label(blueprint)
        add_bp_items(blueprint)

        aws_params_hook = create_generated_options_action(
            "Generate options for CF AWS Specific Params",
            "generate_options_for_aws_specific_params.py",
        )

        create_params(blueprint, template_json, aws_params_hook)

        return blueprint


class CIForm(GitConnectionInfoForm):
    def __init__(self, *args, **kwargs):
        self.initial_instance = kwargs.get("instance")
        super().__init__(*args, **kwargs)
        self.fields["labels"].help_text = _lazy(
            "Please apply either github or gitlab label."
        )

    def clean(self):
        labels = self.cleaned_data.get("labels")

        valid = False
        for label in labels:
            if label.name.lower() in ["github", "gitlab"]:
                if valid:
                    raise forms.ValidationError(
                        {
                            "labels": [
                                "Please only specify either github or gitlab, not both."
                            ]
                        }
                    )
                else:
                    valid = True

        if not valid:
            raise forms.ValidationError(
                {"labels": ["Label of either github or gitlab is required"]}
            )

        new_name = self.cleaned_data.get("name")
        # NOTE: the ModelForm's clean method will validate for existence of this field
        # Only validate the name if adding (no instance) or name has been edited
        if new_name and (not self.instance or new_name != self.instance.name):
            if ConnectionInfo.objects.filter(name=new_name).count() > 0:
                raise forms.ValidationError("Connection Info name is not unique")

        return super().clean()

    def save(self, *args, **kwargs):
        git_connection_info = super().save(*args, **kwargs)
        return git_connection_info
