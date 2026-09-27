import pytest
from strutils.slug import slugify


def test_basic_slug():
    assert slugify("Hello World") == "hello-world"


def test_punctuation_stripped():
    assert slugify("Hello, World!") == "hello-world"


def test_custom_separator():
    assert slugify("Hello World", separator="_") == "hello_world"


def test_consecutive_spaces_collapsed():
    # Failing test: currently produces "hello---world" because consecutive spaces are not collapsed
    assert slugify("hello   world") == "hello-world"
