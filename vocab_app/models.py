from __future__ import annotations

from datetime import date, datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from . import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    words = db.relationship("UserWord", back_populates="user", cascade="all, delete-orphan")
    sessions = db.relationship("QuizSession", back_populates="user", cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Vocabulary(db.Model):
    __tablename__ = "vocabulary"

    id = db.Column(db.Integer, primary_key=True)
    source_word = db.Column(db.String(255), nullable=False, index=True)
    primary_translation = db.Column(db.String(255), nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    accepted_answers = db.relationship(
        "AcceptedAnswer",
        back_populates="vocabulary",
        cascade="all, delete-orphan",
        order_by="AcceptedAnswer.answer_text",
    )
    user_words = db.relationship("UserWord", back_populates="vocabulary", cascade="all, delete-orphan")

    def all_answers(self) -> list[str]:
        answers = [self.primary_translation]
        answers.extend(answer.answer_text for answer in self.accepted_answers)
        seen = set()
        unique = []
        for answer in answers:
            key = answer.casefold().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(answer)
        return unique


class AcceptedAnswer(db.Model):
    __tablename__ = "accepted_answers"

    id = db.Column(db.Integer, primary_key=True)
    vocabulary_id = db.Column(db.Integer, db.ForeignKey("vocabulary.id"), nullable=False, index=True)
    answer_text = db.Column(db.String(255), nullable=False)

    vocabulary = db.relationship("Vocabulary", back_populates="accepted_answers")


class UserWord(db.Model):
    __tablename__ = "user_words"
    __table_args__ = (db.UniqueConstraint("user_id", "vocabulary_id", name="uq_user_vocabulary"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    vocabulary_id = db.Column(db.Integer, db.ForeignKey("vocabulary.id"), nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    learning_state = db.Column(db.String(20), default="new", nullable=False)
    review_level = db.Column(db.Integer, default=0, nullable=False)
    next_review = db.Column(db.Date, nullable=True)
    last_review = db.Column(db.Date, nullable=True)
    date_added_to_testing_set = db.Column(db.Date, default=date.today, nullable=True)

    user = db.relationship("User", back_populates="words")
    vocabulary = db.relationship("Vocabulary", back_populates="user_words")

    @property
    def is_learning(self) -> bool:
        return self.active and self.learning_state in {"new", "learning1", "learning2"}

    @property
    def is_review(self) -> bool:
        return self.active and self.learning_state == "graduated"

    @property
    def display_state(self) -> str:
        return {
            "new": "New",
            "learning1": "Learning 1",
            "learning2": "Learning 2",
            "graduated": "Graduated",
        }.get(self.learning_state, self.learning_state)


class QuizSession(db.Model):
    __tablename__ = "quiz_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    mode = db.Column(db.String(20), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", back_populates="sessions")
    answers = db.relationship("QuizAnswer", back_populates="session", cascade="all, delete-orphan")

    @property
    def accuracy(self) -> float:
        if not self.answers:
            return 0.0
        correct = sum(1 for answer in self.answers if answer.correct)
        return correct / len(self.answers) * 100


class QuizAnswer(db.Model):
    __tablename__ = "quiz_answers"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("quiz_sessions.id"), nullable=False, index=True)
    vocabulary_id = db.Column(db.Integer, db.ForeignKey("vocabulary.id"), nullable=False, index=True)
    user_answer = db.Column(db.String(255), nullable=False, default="")
    matched_answer = db.Column(db.String(255), nullable=True)
    similarity_score = db.Column(db.Float, nullable=True)
    correct = db.Column(db.Boolean, nullable=False, default=False)
    answered_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    session = db.relationship("QuizSession", back_populates="answers")
    vocabulary = db.relationship("Vocabulary")

