import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class LeaderboardConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.contest_id = self.scope['url_route']['kwargs']['contest_id']
        self.group_name = f'leaderboard_{self.contest_id}'
        await self.channel_layer.group_add(
            self.group_name, self.channel_name
        )
        await self.accept()
        data = await self.get_leaderboard()
        await self.send(text_data=json.dumps(
            {'type': 'leaderboard', 'data': data}
        ))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name, self.channel_name
        )

    @database_sync_to_async
    def get_leaderboard(self):
        from .views import _compute_rankings
        return _compute_rankings(self.contest_id)

    async def leaderboard_update(self, event):
        data = await self.get_leaderboard()
        await self.send(text_data=json.dumps(
            {'type': 'leaderboard', 'data': data}
        ))