from utils.facebook_extractor import extract_author


def test_extract_author_returns_null_url_for_alias_actor_with_profile_id_url():
    node = {
        "comet_sections": {
            "content": {
                "story": {
                    "actors": [
                        {
                            "name": "Chịp-chịp",
                            "url": "https://www.facebook.com/profile.php?id=1691781145436199",
                            "id": "1691781145436199",
                            "__typename": "User",
                        }
                    ]
                }
            }
        },
        "feedback": {},
    }

    author = extract_author(node)

    assert author["author_name"] == "Chịp-chịp"
    assert author["author_url"] is None
    assert author["source_type"] == "user"


def test_extract_author_returns_null_url_for_anonymous_actor():
    node = {
        "comet_sections": {
            "content": {
                "story": {
                    "actors": [
                        {
                            "name": "Anonymous",
                            "url": "https://www.facebook.com/profile.php?id=1234567890",
                            "id": "1234567890",
                            "__typename": "User",
                        }
                    ]
                }
            }
        },
        "feedback": {},
    }

    author = extract_author(node)

    assert author["author_name"] == "Anonymous"
    assert author["author_url"] is None
    assert author["source_type"] == "user"


def test_extract_author_returns_null_url_for_group_anon_author_profile():
    node = {
        "feedback": {
            "owning_profile": {
                "__typename": "GroupAnonAuthorProfile",
                "name": "Chịp-chịp",
                "id": "1691781145436199",
            }
        }
    }

    author = extract_author(node)

    assert author["author_name"] == "Chịp-chịp"
    assert author["author_url"] is None
    assert author["source_type"] == "group"
