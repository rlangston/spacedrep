from __future__ import annotations

from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, or_

from . import db
from .models import AcceptedAnswer, QuizAnswer, QuizSession, User, UserWord, Vocabulary, utcnow
from .services import apply_test_result, build_session_queue, record_answer, validate_answer

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("main.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Username and password are required.", "danger")
        elif User.query.filter_by(username=username).first():
            flash("That username is already taken.", "danger")
        else:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("main.dashboard"))
    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user)
            return redirect(request.args.get("next") or url_for("main.dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("auth/login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    today = date.today()
    active = UserWord.query.filter_by(user_id=current_user.id, active=True)
    stats = {
        "total_vocab": Vocabulary.query.count(),
        "active": active.count(),
        "learning": active.filter(UserWord.learning_state.in_(["new", "learning1", "learning2"])).count(),
        "review": active.filter_by(learning_state="graduated").count(),
        "due": active.filter(UserWord.learning_state == "graduated", UserWord.next_review <= today).count(),
    }
    recent_words = Vocabulary.query.order_by(Vocabulary.created_at.desc()).limit(5).all()
    recent_sessions = (
        QuizSession.query.filter_by(user_id=current_user.id)
        .order_by(QuizSession.started_at.desc())
        .limit(5)
        .all()
    )
    return render_template("dashboard.html", stats=stats, recent_words=recent_words, recent_sessions=recent_sessions)


@bp.route("/vocabulary")
@login_required
def vocabulary():
    q = request.args.get("q", "").strip()
    filter_name = request.args.get("filter", "all")
    query = Vocabulary.query.outerjoin(UserWord, (UserWord.vocabulary_id == Vocabulary.id) & (UserWord.user_id == current_user.id))
    if q:
        like = f"%{q}%"
        query = query.outerjoin(AcceptedAnswer).filter(
            or_(
                Vocabulary.source_word.ilike(like),
                Vocabulary.primary_translation.ilike(like),
                AcceptedAnswer.answer_text.ilike(like),
            )
        )
    today = date.today()
    if filter_name == "active":
        query = query.filter(UserWord.active.is_(True))
    elif filter_name == "recent":
        query = query.order_by(Vocabulary.created_at.desc())
    elif filter_name == "new":
        query = query.filter(UserWord.learning_state == "new", UserWord.active.is_(True))
    elif filter_name == "learning":
        query = query.filter(UserWord.learning_state.in_(["new", "learning1", "learning2"]), UserWord.active.is_(True))
    elif filter_name == "graduated":
        query = query.filter(UserWord.learning_state == "graduated", UserWord.active.is_(True))
    elif filter_name == "due":
        query = query.filter(UserWord.learning_state == "graduated", UserWord.next_review <= today, UserWord.active.is_(True))
    if filter_name != "recent":
        query = query.order_by(Vocabulary.source_word)
    rows = query.distinct().all()
    user_words = {
        uw.vocabulary_id: uw
        for uw in UserWord.query.filter_by(user_id=current_user.id).all()
    }
    return render_template("vocabulary/list.html", rows=rows, user_words=user_words, q=q, filter_name=filter_name)


@bp.route("/vocabulary/add", methods=["GET", "POST"])
@login_required
def add_word():
    if request.method == "POST":
        vocab = Vocabulary(
            source_word=request.form.get("source_word", "").strip(),
            primary_translation=request.form.get("primary_translation", "").strip(),
            notes=request.form.get("notes", "").strip() or None,
        )
        if not vocab.source_word or not vocab.primary_translation:
            flash("Source word and primary translation are required.", "danger")
        else:
            db.session.add(vocab)
            add_answers(vocab, request.form.get("accepted_answers", ""))
            db.session.commit()
            flash("Word added.", "success")
            return redirect(url_for("main.vocabulary"))
    return render_template("vocabulary/form.html", vocab=None)


@bp.route("/vocabulary/<int:vocab_id>")
@login_required
def word_detail(vocab_id: int):
    vocab = Vocabulary.query.get_or_404(vocab_id)
    user_word = UserWord.query.filter_by(user_id=current_user.id, vocabulary_id=vocab.id).first()
    answers = (
        vocab_answer_query(vocab.id)
        .order_by(db.text("answered_at DESC"))
        .limit(20)
        .all()
    )
    total = vocab_answer_query(vocab.id).count()
    correct = vocab_answer_query(vocab.id).filter(QuizAnswer.correct.is_(True)).count()
    success_rate = (correct / total * 100) if total else 0
    return render_template("vocabulary/detail.html", vocab=vocab, user_word=user_word, answers=answers, success_rate=success_rate)


@bp.route("/vocabulary/<int:vocab_id>/edit", methods=["GET", "POST"])
@login_required
def edit_word(vocab_id: int):
    vocab = Vocabulary.query.get_or_404(vocab_id)
    if request.method == "POST":
        vocab.source_word = request.form.get("source_word", "").strip()
        vocab.primary_translation = request.form.get("primary_translation", "").strip()
        vocab.notes = request.form.get("notes", "").strip() or None
        if not vocab.source_word or not vocab.primary_translation:
            flash("Source word and primary translation are required.", "danger")
        else:
            vocab.accepted_answers.clear()
            add_answers(vocab, request.form.get("accepted_answers", ""))
            db.session.commit()
            flash("Word updated.", "success")
            return redirect(url_for("main.word_detail", vocab_id=vocab.id))
    return render_template("vocabulary/form.html", vocab=vocab)


@bp.post("/vocabulary/<int:vocab_id>/delete")
@login_required
def delete_word(vocab_id: int):
    vocab = Vocabulary.query.get_or_404(vocab_id)
    source_word = vocab.source_word
    deleted = delete_vocabulary_ids([vocab.id])
    prune_deleted_vocabulary_from_session([vocab.id])
    db.session.commit()
    if deleted:
        flash(f"Deleted {source_word} and related progress/history records.", "success")
    else:
        flash("That word was already deleted.", "info")
    return redirect(url_for("main.vocabulary"))


@bp.post("/vocabulary/<int:vocab_id>/reset")
@login_required
def reset_word(vocab_id: int):
    user_word = UserWord.query.filter_by(user_id=current_user.id, vocabulary_id=vocab_id).first_or_404()
    user_word.learning_state = "new"
    user_word.review_level = 0
    user_word.next_review = None
    user_word.last_review = None
    db.session.commit()
    flash("Progress reset.", "success")
    return redirect(url_for("main.word_detail", vocab_id=vocab_id))


@bp.post("/vocabulary/<int:vocab_id>/remove")
@login_required
def remove_word(vocab_id: int):
    user_word = UserWord.query.filter_by(user_id=current_user.id, vocabulary_id=vocab_id).first_or_404()
    user_word.active = False
    db.session.commit()
    flash("Word removed from the active testing set.", "success")
    return redirect(url_for("main.word_detail", vocab_id=vocab_id))


@bp.route("/bulk-import", methods=["GET", "POST"])
@login_required
def bulk_import():
    if request.method == "POST":
        count = 0
        for line in request.form.get("bulk_text", "").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 2 and parts[0] and parts[1]:
                vocab = Vocabulary(source_word=parts[0], primary_translation=parts[1], notes=parts[3] if len(parts) > 3 else None)
                db.session.add(vocab)
                add_answers(vocab, parts[2] if len(parts) > 2 else "")
                count += 1
        db.session.commit()
        flash(f"{count} words imported.", "success")
        return redirect(url_for("main.vocabulary"))
    return render_template("vocabulary/bulk_import.html")


@bp.route("/administration/data", methods=["GET", "POST"])
@login_required
def data_management():
    if request.method == "POST":
        action = request.form.get("action", "")
        confirmation = request.form.get("confirmation", "").strip()

        if action == "delete_history":
            if confirmation != "DELETE HISTORY":
                flash("Type DELETE HISTORY to confirm deleting your session history.", "danger")
            else:
                count = delete_user_history(current_user.id)
                db.session.commit()
                flash(f"Deleted {count} answer records and your session history.", "success")
                return redirect(url_for("main.data_management"))

        elif action == "delete_progress":
            if confirmation != "RESET PROGRESS":
                flash("Type RESET PROGRESS to confirm clearing your active set and schedules.", "danger")
            else:
                count = UserWord.query.filter_by(user_id=current_user.id).delete(synchronize_session=False)
                db.session.commit()
                flash(f"Deleted progress records for {count} words.", "success")
                return redirect(url_for("main.data_management"))

        elif action == "delete_inactive_words":
            if confirmation != "DELETE INACTIVE WORDS":
                flash("Type DELETE INACTIVE WORDS to confirm deleting inactive vocabulary.", "danger")
            else:
                active_ids = {
                    row[0]
                    for row in db.session.query(UserWord.vocabulary_id)
                    .filter(UserWord.active.is_(True))
                    .distinct()
                    .all()
                }
                query = Vocabulary.query
                if active_ids:
                    query = query.filter(~Vocabulary.id.in_(active_ids))
                vocab_ids = [row[0] for row in query.with_entities(Vocabulary.id).all()]
                deleted = delete_vocabulary_ids(vocab_ids)
                db.session.commit()
                flash(f"Deleted {deleted} inactive vocabulary words and related records.", "success")
                return redirect(url_for("main.data_management"))

        elif action == "delete_all_words":
            if confirmation != "DELETE ALL WORDS":
                flash("Type DELETE ALL WORDS to confirm deleting the entire vocabulary list.", "danger")
            else:
                count = Vocabulary.query.count()
                QuizAnswer.query.delete(synchronize_session=False)
                QuizSession.query.delete(synchronize_session=False)
                UserWord.query.delete(synchronize_session=False)
                AcceptedAnswer.query.delete(synchronize_session=False)
                Vocabulary.query.delete(synchronize_session=False)
                db.session.commit()
                flash(f"Deleted all {count} vocabulary words, all progress, and all session history.", "success")
                return redirect(url_for("main.data_management"))

    stats = cleanup_stats(current_user.id)
    return render_template("admin/data_management.html", stats=stats)


@bp.route("/advance", methods=["GET", "POST"])
@login_required
def advance():
    active_ids = {uw.vocabulary_id for uw in UserWord.query.filter_by(user_id=current_user.id, active=True)}
    inactive_query = Vocabulary.query
    if active_ids:
        inactive_query = inactive_query.filter(~Vocabulary.id.in_(active_ids))
    next_words = inactive_query.order_by(func.random()).limit(10).all()
    inactive_remaining = inactive_query.count()
    if request.method == "POST":
        for vocab in next_words:
            existing = UserWord.query.filter_by(user_id=current_user.id, vocabulary_id=vocab.id).first()
            if existing:
                existing.active = True
                existing.learning_state = "new"
                existing.review_level = 0
                existing.next_review = None
                existing.last_review = None
                existing.date_added_to_testing_set = date.today()
            else:
                db.session.add(UserWord(user=current_user, vocabulary=vocab, active=True, learning_state="new"))
        db.session.commit()
        flash(f"{len(next_words)} words successfully added to the Learning Phase.", "success")
        return redirect(url_for("main.advance_done"))
    active_count = UserWord.query.filter_by(user_id=current_user.id, active=True).count()
    return render_template("advance/confirm.html", active_count=active_count, inactive_remaining=inactive_remaining, next_words=next_words)


@bp.route("/advance/done")
@login_required
def advance_done():
    return render_template("advance/done.html")


@bp.route("/study/<mode>/start", methods=["GET", "POST"])
@login_required
def start_session(mode: str):
    if mode not in {"test", "practice"}:
        flash("Unknown study mode.", "danger")
        return redirect(url_for("main.dashboard"))
    queue = build_session_queue(current_user)
    if request.method == "POST":
        if not queue:
            flash("No learning words or due reviews are available.", "info")
            return redirect(url_for("main.dashboard"))
        quiz_session = QuizSession(user=current_user, mode=mode)
        db.session.add(quiz_session)
        db.session.commit()
        session[f"quiz_{quiz_session.id}"] = [vocab.id for vocab in queue]
        return redirect(url_for("main.quiz_question", session_id=quiz_session.id))
    return render_template("study/start.html", mode=mode, queue=queue)


@bp.route("/study/session/<int:session_id>", methods=["GET", "POST"])
@login_required
def quiz_question(session_id: int):
    quiz_session = QuizSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    queue = session.get(f"quiz_{session_id}", [])
    answered_count = len(quiz_session.answers)
    if not queue:
        quiz_session.completed_at = quiz_session.completed_at or utcnow()
        db.session.commit()
        return redirect(url_for("main.session_detail", session_id=session_id))

    vocab = db.session.get(Vocabulary, queue[0])
    if request.method == "POST":
        user_word = None
        previous_progress = None
        if quiz_session.mode == "practice":
            validation = validate_answer(request.form.get("user_answer", ""), vocab.all_answers())
        else:
            validation = validate_answer(request.form.get("user_answer", ""), vocab.all_answers())
            user_word = UserWord.query.filter_by(user_id=current_user.id, vocabulary_id=vocab.id, active=True).first()
            if user_word:
                previous_progress = snapshot_user_word(user_word)
                apply_test_result(user_word, validation.correct)
        answer = record_answer(quiz_session, vocab, request.form.get("user_answer", ""), validation)
        db.session.add(answer)
        db.session.flush()
        if quiz_session.mode == "test" and previous_progress is not None:
            session[f"override_answer_{answer.id}"] = previous_progress
        queue.pop(0)
        session[f"quiz_{session_id}"] = queue
        if not queue:
            quiz_session.completed_at = utcnow()
        db.session.commit()
        return render_template(
            "study/result.html",
            quiz_session=quiz_session,
            vocab=vocab,
            validation=validation,
            answer=answer,
            remaining=len(queue),
        )

    total = answered_count + len(queue)
    return render_template("study/question.html", quiz_session=quiz_session, vocab=vocab, number=answered_count + 1, total=total)


@bp.post("/study/answer/<int:answer_id>/mark/<result>")
@login_required
def override_answer_result(answer_id: int, result: str):
    if result not in {"correct", "incorrect"}:
        flash("Unknown override result.", "danger")
        return redirect(url_for("main.history"))

    answer = QuizAnswer.query.join(QuizSession).filter(
        QuizAnswer.id == answer_id,
        QuizSession.user_id == current_user.id,
    ).first_or_404()
    corrected_value = result == "correct"

    if answer.correct == corrected_value:
        flash(f"That answer is already marked {result}.", "info")
    else:
        if answer.session.mode == "test":
            snapshot = session.pop(f"override_answer_{answer.id}", None)
            if not snapshot:
                flash("That test answer can no longer be overridden.", "warning")
                remaining = session.get(f"quiz_{answer.session_id}", [])
                if remaining:
                    return redirect(url_for("main.quiz_question", session_id=answer.session_id))
                return redirect(url_for("main.session_detail", session_id=answer.session_id))

            user_word = UserWord.query.filter_by(
                user_id=current_user.id,
                vocabulary_id=answer.vocabulary_id,
            ).first()
            if user_word:
                restore_user_word(user_word, snapshot)
                apply_test_result(user_word, corrected_value)

        answer.correct = corrected_value
        db.session.commit()
        flash(f"Answer marked {result}.", "success")

    remaining = session.get(f"quiz_{answer.session_id}", [])
    if remaining:
        return redirect(url_for("main.quiz_question", session_id=answer.session_id))
    return redirect(url_for("main.session_detail", session_id=answer.session_id))


@bp.route("/history")
@login_required
def history():
    sessions = QuizSession.query.filter_by(user_id=current_user.id).order_by(QuizSession.started_at.desc()).all()
    return render_template("history/list.html", sessions=sessions)


@bp.route("/history/<int:session_id>")
@login_required
def session_detail(session_id: int):
    quiz_session = QuizSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    return render_template("history/detail.html", quiz_session=quiz_session)


@bp.route("/profile")
@login_required
def profile():
    return render_template("auth/profile.html")


def add_answers(vocab: Vocabulary, raw_answers: str) -> None:
    seen = {vocab.primary_translation.casefold().strip()}
    for answer in raw_answers.replace("\n", ";").split(";"):
        clean = answer.strip()
        key = clean.casefold()
        if clean and key not in seen:
            vocab.accepted_answers.append(AcceptedAnswer(answer_text=clean))
            seen.add(key)


def cleanup_stats(user_id: int) -> dict[str, int]:
    active_ids = {
        row[0]
        for row in db.session.query(UserWord.vocabulary_id)
        .filter(UserWord.active.is_(True))
        .distinct()
        .all()
    }
    inactive_query = Vocabulary.query
    if active_ids:
        inactive_query = inactive_query.filter(~Vocabulary.id.in_(active_ids))
    session_ids = [row[0] for row in QuizSession.query.with_entities(QuizSession.id).filter_by(user_id=user_id).all()]
    answer_count = 0
    if session_ids:
        answer_count = QuizAnswer.query.filter(QuizAnswer.session_id.in_(session_ids)).count()
    return {
        "all_words": Vocabulary.query.count(),
        "accepted_answers": AcceptedAnswer.query.count(),
        "inactive_words": inactive_query.count(),
        "user_progress": UserWord.query.filter_by(user_id=user_id).count(),
        "user_sessions": len(session_ids),
        "user_answers": answer_count,
        "all_sessions": QuizSession.query.count(),
        "all_answers": QuizAnswer.query.count(),
    }


def delete_user_history(user_id: int) -> int:
    session_ids = [row[0] for row in QuizSession.query.with_entities(QuizSession.id).filter_by(user_id=user_id).all()]
    answer_count = 0
    if session_ids:
        answer_count = QuizAnswer.query.filter(QuizAnswer.session_id.in_(session_ids)).delete(synchronize_session=False)
    QuizSession.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    return answer_count


def delete_vocabulary_ids(vocab_ids: list[int]) -> int:
    if not vocab_ids:
        return 0
    QuizAnswer.query.filter(QuizAnswer.vocabulary_id.in_(vocab_ids)).delete(synchronize_session=False)
    UserWord.query.filter(UserWord.vocabulary_id.in_(vocab_ids)).delete(synchronize_session=False)
    AcceptedAnswer.query.filter(AcceptedAnswer.vocabulary_id.in_(vocab_ids)).delete(synchronize_session=False)
    return Vocabulary.query.filter(Vocabulary.id.in_(vocab_ids)).delete(synchronize_session=False)


def prune_deleted_vocabulary_from_session(vocab_ids: list[int]) -> None:
    deleted_ids = set(vocab_ids)
    changed = False
    for key, value in list(session.items()):
        if not key.startswith("quiz_") or not isinstance(value, list):
            continue
        pruned = [vocab_id for vocab_id in value if vocab_id not in deleted_ids]
        if len(pruned) != len(value):
            session[key] = pruned
            changed = True
    if changed:
        session.modified = True


def snapshot_user_word(user_word: UserWord) -> dict[str, str | int | bool | None]:
    return {
        "active": user_word.active,
        "learning_state": user_word.learning_state,
        "review_level": user_word.review_level,
        "next_review": user_word.next_review.isoformat() if user_word.next_review else None,
        "last_review": user_word.last_review.isoformat() if user_word.last_review else None,
        "date_added_to_testing_set": (
            user_word.date_added_to_testing_set.isoformat()
            if user_word.date_added_to_testing_set
            else None
        ),
    }


def restore_user_word(user_word: UserWord, snapshot: dict[str, str | int | bool | None]) -> None:
    user_word.active = bool(snapshot["active"])
    user_word.learning_state = str(snapshot["learning_state"])
    user_word.review_level = int(snapshot["review_level"])
    user_word.next_review = parse_snapshot_date(snapshot["next_review"])
    user_word.last_review = parse_snapshot_date(snapshot["last_review"])
    user_word.date_added_to_testing_set = parse_snapshot_date(snapshot["date_added_to_testing_set"])


def parse_snapshot_date(value: str | int | bool | None):
    if not value or not isinstance(value, str):
        return None
    return date.fromisoformat(value)


def vocab_answer_query(vocab_id: int):
    return QuizAnswer.query.join(QuizSession).filter(
        QuizAnswer.vocabulary_id == vocab_id,
        QuizSession.user_id == current_user.id,
    )
