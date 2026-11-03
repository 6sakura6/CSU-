"""Offline Fetch interception for CDP tests; no request is continued to the Internet."""
import base64
import fnmatch
import json as json_module

from cdp_browser import BrowserContext


class FixtureRequest:
    def __init__(self, request):
        self.url = request['url']
        self.method = request['method']
        self.post_data = request.get('postData')

    @property
    def post_data_json(self):
        return json_module.loads(self.post_data) if self.post_data else None


class FixtureRoute:
    def __init__(self, page, params):
        self.page, self.params = page, params
        self.request = FixtureRequest(params['request'])

    def fulfill(self, *, body='', content_type='application/json', json=None, status=200, headers=None):
        if json is not None:
            body = json_module.dumps(json, ensure_ascii=False)
            content_type = 'application/json; charset=utf-8'
        raw = body.encode('utf-8') if isinstance(body, str) else body
        self.page.context.client.send('Fetch.fulfillRequest', {'requestId': self.params['requestId'],
            'responseCode': status, 'responseHeaders': [{'name': 'Content-Type', 'value': content_type},
                *({'name': key, 'value': value} for key, value in (headers or {}).items())],
            'body': base64.b64encode(raw).decode()}, self.page.session)

    def abort(self):
        self.page.context.client.send('Fetch.failRequest', {'requestId': self.params['requestId'],
            'errorReason': 'Aborted'}, self.page.session)


class FixtureRoutes:
    def __init__(self):
        self.routes = []
        self.requests = []

    def handle(self, page, params):
        route = FixtureRoute(page, params)
        self.requests.append((route.request.method, route.request.url))
        for pattern, callback in reversed(self.routes):
            if fnmatch.fnmatchcase(route.request.url, pattern):
                callback(route)
                return
        route.abort()


class FixtureContext(BrowserContext):
    def __init__(self, client):
        super().__init__(client, fixture=FixtureRoutes())

    def route(self, pattern, callback):
        self.fixture.routes.append((pattern, callback))


class FixtureBrowser:
    def __init__(self, process):
        self.process = process

    def new_context(self, **_):
        return FixtureContext(self.process.client)

    def close(self):
        self.process.close()
