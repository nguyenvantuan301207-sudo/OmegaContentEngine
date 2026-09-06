from sqlalchemy import String

from omega.infrastructure.models import TopicCandidate


def test_topic_candidate_source_name_schema():
    column = TopicCandidate.__table__.c.source_name

    assert column.nullable is False
    assert isinstance(column.type, String)
    assert column.type.length == 100
    assert column.server_default is not None
    assert column.server_default.arg == "user_input"
