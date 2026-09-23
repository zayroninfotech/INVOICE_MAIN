import mongoengine as me
from datetime import datetime, timezone
from django.contrib.sessions.backends.base import SessionBase, CreateError
from django.utils import timezone as dj_tz


class MongoSessionDoc(me.Document):
    session_key  = me.StringField(primary_key=True, max_length=40)
    session_data = me.StringField()
    expire_date  = me.DateTimeField()

    meta = {
        'collection': 'django_sessions',
        'indexes': [{'fields': ['expire_date'], 'expireAfterSeconds': 0}],
    }


class SessionStore(SessionBase):

    def _get_doc(self):
        try:
            return MongoSessionDoc.objects.get(session_key=self.session_key)
        except MongoSessionDoc.DoesNotExist:
            return None

    def load(self):
        doc = self._get_doc()
        if doc is None or (doc.expire_date and doc.expire_date < datetime.now(tz=timezone.utc)):
            self._session_key = None
            return self.create()
        return self.decode(doc.session_data)

    def exists(self, session_key):
        return MongoSessionDoc.objects(session_key=session_key).count() > 0

    def create(self):
        while True:
            self._session_key = self._get_new_session_key()
            try:
                self.save(must_create=True)
            except CreateError:
                continue
            self.modified = True
            return

    def save(self, must_create=False):
        if self.session_key is None:
            return self.create()
        data = self.encode(self._get_session(no_load=must_create))
        expire_date = self.get_expiry_date()
        if must_create:
            if self.exists(self.session_key):
                raise CreateError
            MongoSessionDoc(
                session_key=self.session_key,
                session_data=data,
                expire_date=expire_date,
            ).save()
        else:
            MongoSessionDoc.objects(session_key=self.session_key).update_one(
                set__session_data=data,
                set__expire_date=expire_date,
                upsert=True,
            )

    def delete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        MongoSessionDoc.objects(session_key=session_key).delete()

    @classmethod
    def clear_expired(cls):
        MongoSessionDoc.objects(expire_date__lt=datetime.now(tz=timezone.utc)).delete()
