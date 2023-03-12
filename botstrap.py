import os
import re
from pathlib import Path

from dotenv import load_dotenv
from httpx import Client, HTTPStatusError, Response

from bot.constants import Webhooks, _Categories, _Channels, _Roles
from bot.log import get_logger

load_dotenv()
log = get_logger("Config Bootstrapper")

env_file_path = Path(".env.server")
BOT_TOKEN = os.getenv("BOT_TOKEN", None)
GUILD_ID = os.getenv("GUILD_ID", None)

COMMUNITY_FEATURE = "COMMUNITY"
PYTHON_HELP_NAME = _Channels.__fields__["python_help"].name
GUILD_FORUM_TYPE = 15


if not BOT_TOKEN:
    message = (
        "Couldn't find BOT_TOKEN in the environment variables."
        "Make sure to add it to the `.env` file likewise: `BOT_TOKEN=value_of_your_bot_token`"
    )
    log.warning(message)
    raise ValueError(message)

if not GUILD_ID:
    message = (
        "Couldn't find GUILD_ID in the environment variables."
        "Make sure to add it to the `.env` file likewise: `GUILD_ID=value_of_your_discord_server_id`"
    )
    log.warning(message)
    raise ValueError(message)


class DiscordClient(Client):
    """An HTTP client to communicate with Discord's APIs."""

    def __init__(self):
        super().__init__(
            base_url="https://discord.com/api/v10",
            headers={"Authorization": f"Bot {BOT_TOKEN}"},
            event_hooks={"response": [self._raise_for_status]}
        )

    @staticmethod
    def _raise_for_status(response: Response) -> None:
        response.raise_for_status()


def is_community_server(guild_id: int | str, client: DiscordClient) -> bool:
    """A predicate that indicates whether a server has the COMMUNITY feature or not"""
    response = client.get(f"/guilds/{guild_id}")
    return COMMUNITY_FEATURE in response.json()["features"]


def upgrade_server_to_community(guild_id: int | str, client: DiscordClient) -> bool:
    """Transforms a server into a community one.

    Return true if the server has been correctly upgraded, False otherwise.
    """

    payload = {"features": [COMMUNITY_FEATURE]}
    response = client.patch(f"/guilds/{guild_id}", json=payload)

    return 200 <= response.status_code < 300


def create_forum_channel(channel_name_: str, guild_id: str, client: DiscordClient, category_id_: int | None = None):
    """Creates a new forum channel"""

    payload = {"name": channel_name_, "type": GUILD_FORUM_TYPE}
    if category_id_:
        payload["parent_id"] = category_id_

    response = client.post(f"/guilds/{guild_id}/channels", json=payload)

    return response.json()["id"]


def is_forum_channel(channel_id_: str, client: DiscordClient) -> bool:
    """A boolean that indicates if a channel is of type GUILD_FORUM"""

    response = client.get(f"/channels/{channel_id_}")
    return response.json()["type"] == GUILD_FORUM_TYPE


def delete_channel(channel_id_: id, client: DiscordClient):
    """Upgrades a channel to a channel of type GUILD FORUM"""

    response = client.delete(f"/channels/{channel_id_}")

    return response.json()


def get_all_roles(guild_id: int | str, client: DiscordClient) -> dict:
    """Fetches all the roles in a guild."""
    result = {}

    response = client.get(f"guilds/{guild_id}/roles")
    roles = response.json()

    for role in roles:
        name = "_".join(part.lower() for part in role["name"].split(" ")).replace("-", "_")
        result[name] = role["id"]

    return result


def get_all_channels_and_categories(
        guild_id: int | str,
        client: DiscordClient
) -> tuple[dict[str, str], dict[str, str]]:
    """Fetches all the text channels & categories in a guild."""
    off_topic_channel_name_regex = r"ot\d{1}(_.*)+"
    off_topic_count = 0
    channels = {}  # could be text channels only as well
    categories = {}

    response = client.get(f"guilds/{guild_id}/channels")
    server_channels = response.json()

    for channel in server_channels:
        channel_type = channel["type"]
        name = "_".join(part.lower() for part in channel["name"].split(" ")).replace("-", "_")
        if re.match(off_topic_channel_name_regex, name):
            name = f"off_topic_{off_topic_count}"
            off_topic_count += 1

        if channel_type == 4:
            categories[name] = channel["id"]
        else:
            channels[name] = channel["id"]

    return channels, categories


def webhook_exists(webhook_id_: int, client: DiscordClient) -> bool:
    """A predicate that indicates whether a webhook exists already or not."""
    try:
        client.get(f"webhooks/{webhook_id_}")
        return True
    except HTTPStatusError:
        return False


def create_webhook(name: str, channel_id_: int, client: DiscordClient) -> str:
    """Creates a new webhook for a particular channel."""
    payload = {"name": name}

    response = client.post(f"channels/{channel_id_}/webhooks", json=payload)
    new_webhook = response.json()
    return new_webhook["id"]


with DiscordClient() as discord_client:
    config_str = "#Roles\n"

    all_roles = get_all_roles(guild_id=GUILD_ID, client=discord_client)

    for role_name in _Roles.__fields__:

        role_id = all_roles.get(role_name, None)
        if not role_id:
            log.warning(f"Couldn't find the role {role_name} in the guild, PyDis' default values will be used.")
            continue

        config_str += f"roles_{role_name}={role_id}\n"

    all_channels, all_categories = get_all_channels_and_categories(guild_id=GUILD_ID, client=discord_client)

    config_str += "\n#Channels\n"

    if not is_community_server(GUILD_ID, discord_client):
        upgrade_server_to_community(GUILD_ID, discord_client)

    create_help_channel = True
    if PYTHON_HELP_NAME in all_channels:
        python_help_channel_id = all_channels[PYTHON_HELP_NAME]
        if not is_forum_channel(python_help_channel_id, discord_client):
            delete_channel(python_help_channel_id, discord_client)
        else:
            create_help_channel = False

    if create_help_channel:
        python_help_channel_id = create_forum_channel(PYTHON_HELP_NAME.replace('_', '-'), GUILD_ID, discord_client)

    for channel_name in _Channels.__fields__:
        channel_id = all_channels.get(channel_name, None)
        if not channel_id:
            log.warning(
                f"Couldn't find the channel {channel_name} in the guild, PyDis' default values will be used."
            )
            continue

        config_str += f"channels_{channel_name}={channel_id}\n"
    config_str += f"channels_{PYTHON_HELP_NAME}={python_help_channel_id}\n"

    config_str += "\n#Categories\n"

    for category_name in _Categories.__fields__:
        category_id = all_categories.get(category_name, None)
        if not category_id:
            log.warning(
                f"Couldn't find the category {category_name} in the guild, PyDis' default values will be used."
            )
            continue

        config_str += f"categories_{category_name}={category_id}\n"

    env_file_path.write_text(config_str)

    config_str += "\n#Webhooks\n"

    for webhook_name, webhook_model in Webhooks:
        webhook = webhook_exists(webhook_model.id, client=discord_client)
        if not webhook:
            webhook_channel_id = int(all_channels[webhook_name])
            webhook_id = create_webhook(webhook_name, webhook_channel_id, client=discord_client)
        else:
            webhook_id = webhook_model.id
        config_str += f"webhooks_{webhook_name}__id={webhook_id}\n"
        config_str += f"webhooks_{webhook_name}__channel={all_channels[webhook_name]}\n"

    config_str += "\n#Emojis\n"
    config_str += "emojis_trashcan=🗑️"

    with env_file_path.open("wb") as file:
        file.write(config_str.encode("utf-8"))
