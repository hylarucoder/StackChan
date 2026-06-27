import os
import sys

import uvicorn


class FakeAgent:
    def __init__(self):
        self.started_agent_ids = set()

    async def start(self, channel_name: str, agent_uid: int, user_uid: int, output_audio_codec=None):
        if not channel_name or agent_uid <= 0 or user_uid <= 0:
            raise ValueError("channel_name, agent_uid, and user_uid must be valid")

        agent_id = f"fake-agent-{agent_uid}"
        self.started_agent_ids.add(agent_id)
        return {
            "agent_id": agent_id,
            "channel_name": channel_name,
            "status": "started",
        }

    async def stop(self, agent_id: str):
        if not agent_id:
            raise ValueError("agent_id is required")
        self.started_agent_ids.discard(agent_id)


def fake_generate_convo_ai_token(**_kwargs):
    return "fake-token"


def configure_fake_environment(env):
    env.setdefault("AGORA_APP_ID", "fake-app-id")
    env.setdefault("AGORA_APP_CERTIFICATE", "fake-app-certificate")


def build_fake_app():
    from stackchan_server.stackchan.commands import CommandRouter
    from stackchan_server.apps.native_agent_api import ServerDependencies, create_app

    return create_app(
        ServerDependencies(
            agent=FakeAgent(),
            command_router=CommandRouter(),
            token_generator=fake_generate_convo_ai_token,
            app_id=os.environ["AGORA_APP_ID"],
            app_certificate=os.environ["AGORA_APP_CERTIFICATE"],
        )
    )


def main():
    configure_fake_environment(os.environ)

    server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_root = os.path.join(server_root, "src")
    if src_root not in sys.path:
        sys.path.insert(0, src_root)

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(build_fake_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
