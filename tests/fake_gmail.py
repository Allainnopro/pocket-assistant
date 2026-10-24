"""Just enough of the Gmail API client for sync and the engine's mail check,
recording what it is asked. Read-only, like the real scope."""

import httplib2
from googleapiclient.errors import HttpError


class _Call:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.result


def not_found() -> HttpError:
    return HttpError(httplib2.Response({"status": 404}), b'{"error": {"code": 404, "message": "Not Found"}}')


class FakeGmail:
    def __init__(self, messages, profile, history_id="100"):
        self.by_id = {m["id"]: m for m in messages}
        self.profile = profile
        self.history_id = history_id
        self.deleted: set[str] = set()
        self.queries: list[str] = []
        self.profile_calls = 0
        self.calls: list[str] = []
        self.history_pages: list[dict] = []  # page i is asked for with pageToken str(i)
        self.history_requests: list[dict] = []
        self.history_expired = False

    def users(self):
        return self

    def messages(self):
        return self

    def history(self):
        return _History(self)

    def expire_history(self):
        """Gmail keeps history about a week; an older bookmark gets a 404."""
        self.history_expired = True

    def delete(self, msg_id):
        """The message is gone before it is fetched (deleted or moved on the server)."""
        self.deleted.add(msg_id)

    def getProfile(self, userId):
        self.calls.append("getProfile")
        self.profile_calls += 1
        return _Call({"emailAddress": self.profile, "historyId": self.history_id})

    def list(self, userId, q, pageToken=None, maxResults=None):
        self.calls.append("messages.list")
        self.queries.append(q)
        return _Call({"messages": [{"id": i} for i in self.by_id]})

    def get(self, userId, id, format):
        self.calls.append("messages.get")
        if id in self.deleted or id not in self.by_id:
            return _Call(error=not_found())
        return _Call(self.by_id[id])


class _History:
    def __init__(self, gmail: FakeGmail):
        self.gmail = gmail

    def list(self, userId, startHistoryId, historyTypes=None, maxResults=None, pageToken=None):
        self.gmail.calls.append("history.list")
        self.gmail.history_requests.append({"startHistoryId": startHistoryId, "historyTypes": historyTypes,
                                            "maxResults": maxResults, "pageToken": pageToken})
        if self.gmail.history_expired:
            return _Call(error=not_found())
        if not self.gmail.history_pages:
            return _Call({"historyId": self.gmail.history_id})
        return _Call(self.gmail.history_pages[int(pageToken or 0)])
