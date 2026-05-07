import json
from channels.generic.websocket import AsyncWebsocketConsumer


class SubmissionConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.submission_id = self.scope['url_route']['kwargs']['submission_id']
        self.group_name = f'submission_{self.submission_id}'
        await self.channel_layer.group_add(
            self.group_name, self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name, self.channel_name
        )

    async def submission_result(self, event):
        await self.send(text_data=json.dumps({
            'type': 'result',
            'submission_id': event['submission_id'],
            'status': event['status'],
            'score': event['score'],
        }))