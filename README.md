# SpacedRep Vocabulary

A Flask vocabulary learning application for Old English or any source/English vocabulary pair.

## Setup

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e .
flask --app vocab_app init-db
flask --app vocab_app run
```

Create an account at `/register`, add vocabulary, then use **Advance (+10 Words)** to move words into the active learning set.

## DispatcherMiddleware

The app is built with an app factory and `url_for()` throughout, so it can be mounted below another Flask app:

```python
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from vocab_app import create_app

application = DispatcherMiddleware(parent_app, {
    "/vocabulary": create_app(),
})
```

