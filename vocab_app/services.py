from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from rapidfuzz import fuzz

from .models import QuizAnswer, QuizSession, User, UserWord, Vocabulary

REVIEW_INTERVALS = {
    1: 1,
    2: 3,
    3: 7,
    4: 14,
    5: 30,
    6: 60,
    7: 120,
}
PASS_THRESHOLD = 88


@dataclass
class ValidationResult:
    user_answer: str
    matched_answer: str | None
    similarity_score: float
    correct: bool


def normalize_answer(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def validate_answer(user_answer: str, accepted_answers: list[str]) -> ValidationResult:
    normalized_user = normalize_answer(user_answer)
    best_answer = None
    best_score = 0.0
    for answer in accepted_answers:
        score = fuzz.ratio(normalized_user, normalize_answer(answer))
        if score > best_score:
            best_score = score
            best_answer = answer
    return ValidationResult(
        user_answer=user_answer.strip(),
        matched_answer=best_answer,
        similarity_score=best_score,
        correct=bool(normalized_user) and best_score >= PASS_THRESHOLD,
    )


def build_session_queue(user: User, shuffle: bool = True) -> list[Vocabulary]:
    today = date.today()
    learning = (
        Vocabulary.query.join(UserWord)
        .filter(
            UserWord.user_id == user.id,
            UserWord.active.is_(True),
            UserWord.learning_state.in_(["new", "learning1", "learning2"]),
        )
        .order_by(UserWord.date_added_to_testing_set, Vocabulary.id)
        .all()
    )
    review = (
        Vocabulary.query.join(UserWord)
        .filter(
            UserWord.user_id == user.id,
            UserWord.active.is_(True),
            UserWord.learning_state == "graduated",
            UserWord.next_review <= today,
        )
        .order_by(UserWord.next_review, Vocabulary.id)
        .all()
    )
    if shuffle:
        random.shuffle(learning)
        random.shuffle(review)
    return learning + review


def apply_test_result(user_word: UserWord, correct: bool) -> None:
    today = date.today()
    if user_word.is_learning:
        if not correct:
            return
        if user_word.learning_state == "new":
            user_word.learning_state = "learning1"
        elif user_word.learning_state == "learning1":
            user_word.learning_state = "learning2"
        elif user_word.learning_state == "learning2":
            user_word.learning_state = "graduated"
            user_word.review_level = 1
            user_word.last_review = today
            user_word.next_review = today + timedelta(days=REVIEW_INTERVALS[1])
        return

    if user_word.is_review:
        if correct:
            user_word.review_level = min(user_word.review_level + 1, max(REVIEW_INTERVALS))
        else:
            user_word.review_level = max(user_word.review_level - 2, 1)
        user_word.last_review = today
        user_word.next_review = today + timedelta(days=REVIEW_INTERVALS[user_word.review_level])


def record_answer(
    quiz_session: QuizSession,
    vocabulary: Vocabulary,
    user_answer: str,
    validation: ValidationResult,
) -> QuizAnswer:
    return QuizAnswer(
        session=quiz_session,
        vocabulary=vocabulary,
        user_answer=validation.user_answer or user_answer.strip(),
        matched_answer=validation.matched_answer,
        similarity_score=validation.similarity_score,
        correct=validation.correct,
    )

