"""
Get Options Action for ARM Builder for Environment ID
"""
from accounts.models import Group
from common.methods import set_progress


def get_options_list(field, control_value=None, **kwargs):
    set_progress(f"kwargs: {kwargs}")
    if control_value:
        allowed_envs = [string.strip() for string in control_value.split(",")]
        group_name = kwargs["group"]
        set_progress(f"group: {group_name}")
        group = Group.objects.get(name=group_name)
        envs = group.get_available_environments()
        set_progress(f"Available Envs: {envs}")
        options = [("", "--- Select an Environment ---")]
        for env in envs:
            if env.resource_handler:
                if env.resource_handler.resource_technology:
                    if (
                        env.resource_handler.resource_technology.name == "Amazon "
                        "Web Services"
                    ):
                        if "all_capable" in allowed_envs:
                            options.append((env.id, env.name))
                        elif str(env.id) in allowed_envs:
                            options.append((env.id, env.name))
    else:
        options = [("", "--- Allowed Environments Not Set for Blueprint ---")]
    return options
