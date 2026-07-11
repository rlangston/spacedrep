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

## Study flow

Vocabulary starts out inactive. Inactive words are stored in the shared vocabulary list, but they are not included in test or practice sessions for a user until that user advances them.

Use **Advance (+10 Words)** to add up to 10 inactive words to your active testing set. The app chooses those words at random from the inactive list. When a word is advanced, it enters the learning phase with the state `new`.

Active words move through two broad phases:

- Learning phase: `new`, `learning1`, and `learning2`
- Review phase: `graduated`

Test sessions are what move words through the schedule. A correct answer advances a learning word by one step:

- `new` -> `learning1`
- `learning1` -> `learning2`
- `learning2` -> `graduated`

An incorrect answer in the learning phase leaves the word in its current learning state, so it will appear again in later sessions until it is answered correctly enough times to graduate.

After a word graduates, it moves onto spaced review. The review gaps are:

| Review level | Next review gap |
| --- | --- |
| 1 | 1 day |
| 2 | 3 days |
| 3 | 7 days |
| 4 | 14 days |
| 5 | 30 days |
| 6 | 60 days |
| 7 | 120 days |

A correct answer during review raises the word by one review level, up to level 7. An incorrect answer drops it by two review levels, but never below level 1. The app records the review date and schedules the next review by adding the current level's gap to today's date.

## Tests and practice

Both **Test** and **Practice** sessions can be run multiple times in the same day. Starting a session builds a queue from:

- all active learning words: `new`, `learning1`, and `learning2`
- active graduated words whose `next_review` date is today or earlier

Learning words are available every time you start a session, even if you already answered them earlier that day. That means you can move a new word through `new`, `learning1`, and `learning2` on the same day by running tests repeatedly and answering it correctly each time.

Graduated review words are only included when they are due. If a test answer reschedules a graduated word into the future, it will not appear in later sessions that day unless it becomes due again or its progress is reset.

Practice sessions use the same available-word queue and the same answer checking as tests, including the optional answer reveal. Practice answers are saved in history, but practice does not change learning states, review levels, or next review dates. Use practice when you want extra repetitions without moving words forward or backward in the schedule.

Answers are checked against the primary translation and any accepted answers. The comparison ignores case and extra whitespace, and a similarity score of 88 or higher is marked correct. After an answer, you can override the result from the result screen; test overrides also reapply the scheduling change from the corrected result.

## DispatcherMiddleware

The app is built with an app factory and `url_for()` throughout, so it can be mounted below another Flask app. This repository's top-level `app.py` mounts it at `/vocab`:

```python
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from vocab_app import create_app

application = DispatcherMiddleware(parent_app, {
    "/vocab": create_app(),
})
```
