"""Conversational interview prompts, selected one at a time from known gaps."""

# ruff: noqa: E501 -- Keep authored spoken questions readable as single strings.

from dataclasses import dataclass

from secretary_service.life_knowledge import KnowledgeKind, LifeDomain


@dataclass(frozen=True)
class InterviewTopic:
    """One purposeful assertion key, not a form presented to the user."""

    key: str
    domain: LifeDomain
    kind: KnowledgeKind
    prompt: str
    staleness_days: int | None = 90
    importance: int = 5


def _topics(
    domain: LifeDomain,
    kind: KnowledgeKind,
    questions: tuple[tuple[str, str], ...],
) -> tuple[InterviewTopic, ...]:
    return tuple(
        InterviewTopic(f"{domain.value}.{key}", domain, kind, prompt) for key, prompt in questions
    )


SENSITIVE_DOMAINS = frozenset(
    {
        LifeDomain.FINANCES,
        LifeDomain.RELATIONSHIPS,
        LifeDomain.VALUES,
        LifeDomain.WELLNESS,
    }
)

TOPICS = (
    *_topics(
        LifeDomain.IDENTITY,
        KnowledgeKind.IDENTITY,
        (
            ("name", "Let's start with you. What would you like me to call you?"),
            ("base", "Where are you based? A city or general area is plenty."),
            ("living", "What does your current living situation look like?"),
            (
                "transitions",
                "What stage of life are you in, and are any big transitions happening?",
            ),
            ("attention", "What's taking most of your attention right now?"),
            ("stability", "What feels stable in your life, and what feels uncertain?"),
        ),
    ),
    *_topics(
        LifeDomain.RESPONSIBILITIES,
        KnowledgeKind.COMMITMENT,
        (
            ("inventory", "What responsibilities are you accountable for right now?"),
            ("people", "Who depends on you, and which commitments can't simply be dropped?"),
            (
                "groups",
                "Which organizations or groups are you involved with, and what do you do for each?",
            ),
            ("duration", "Which responsibilities are temporary, and which are ongoing?"),
            ("capacity", "Where do you feel overcommitted?"),
        ),
    ),
    *_topics(
        LifeDomain.WORK,
        KnowledgeKind.FACT,
        (
            ("role", "Tell me about your current work. What's your role and employer?"),
            (
                "schedule",
                "What are your normal work days and hours, and how different is the actual schedule?",
            ),
            ("commute", "What does getting to and from work take, including preparation?"),
            ("experience", "What do you like about your work, and what would you want to change?"),
            (
                "direction",
                "Are you looking to stay, change jobs, build a business, or some combination?",
            ),
            (
                "requirements",
                "What would your next role need to offer in pay, benefits, schedule, and location? Share only what helps.",
            ),
            ("boundaries", "What fields or roles appeal to you, and which would you rule out?"),
            (
                "pipeline",
                "Are there applications, interviews, offers, or career follow-ups in progress?",
            ),
            ("future", "What would career success look like in one, three, five, and ten years?"),
        ),
    ),
    *_topics(
        LifeDomain.FINANCES,
        KnowledgeKind.CONSTRAINT,
        (
            (
                "scope",
                "What level of financial context would you like me to help with? You don't need to share balances or account details.",
            ),
            (
                "cadence",
                "What income cadence and recurring obligations should planning account for?",
            ),
            (
                "goals",
                "Which savings, debt, emergency-fund, investing, or lifestyle goals matter to you?",
            ),
            ("upcoming", "Are there major expenses or financial deadlines coming up?"),
            (
                "system",
                "Do you already use a budgeting system that should remain your source of truth?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.EDUCATION,
        KnowledgeKind.FACT,
        (
            (
                "program",
                "Are you enrolled in school or a learning program? Tell me what you're studying.",
            ),
            ("classes", "What classes, sessions, or assignments are active this term?"),
            (
                "plans",
                "Are you working toward graduation, a transfer, certifications, or a change of program?",
            ),
            (
                "skills",
                "What subjects or skills do you want to develop, and how do you like to learn?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.RELATIONSHIPS,
        KnowledgeKind.RELATIONSHIP,
        (
            (
                "people",
                "Who are the important people you would like me to remember, and how are they part of your life?",
            ),
            ("time", "Which relationships would you like to give more intentional time to?"),
            ("responsibilities", "Are there family or partner responsibilities I should respect?"),
            (
                "occasions",
                "Are there birthdays, anniversaries, or other occasions you'd like help remembering?",
            ),
            ("social", "What does a satisfying social life look like for you?"),
        ),
    ),
    *_topics(
        LifeDomain.VALUES,
        KnowledgeKind.VALUE,
        (
            ("principles", "What matters most to you? What should I never optimize away?"),
            ("good_life", "What makes a good life in your view?"),
            (
                "conflicts",
                "When priorities conflict, which responsibilities or principles come first?",
            ),
            ("practices", "Are there spiritual or philosophical practices you want protected?"),
            (
                "identity",
                "What kind of person are you trying to become, and what compromises are unacceptable?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.GOALS,
        KnowledgeKind.ASPIRATION,
        (
            (
                "dreams",
                "What are your biggest dreams? We can leave them as dreams for now, without turning them into tasks.",
            ),
            ("near", "What would you like your life to look like in six months and one year?"),
            ("far", "How about three, five, and ten years from now?"),
            ("possibility", "What would you pursue if practical constraints disappeared?"),
            ("regret", "What have you wanted for a long time, or would regret never trying?"),
        ),
    ),
    *_topics(
        LifeDomain.ROUTINES,
        KnowledgeKind.ROUTINE,
        (
            (
                "sleep",
                "When do you usually sleep and wake? What times and amount of sleep would you prefer?",
            ),
            ("morning", "Walk me through your morning, including preparation and travel."),
            ("evening", "What does your evening look like, including winding down?"),
            (
                "weekly",
                "Walk me through a typical week: work, school, community, family, and recurring meetings.",
            ),
            ("meals", "How do meals, cooking, and cleanup fit into your days?"),
            ("exercise", "What exercise or movement routines do you want time for?"),
            ("home", "What household chores, errands, and personal care recur?"),
            (
                "rest",
                "What time do you want for hobbies, entertainment, decompression, and doing nothing in particular?",
            ),
            (
                "constraints",
                "For those routines, which times are fixed, preferred, flexible, or optional? Include days, duration, travel, preparation, and anything that must happen first.",
            ),
            (
                "minimums",
                "How often does each important routine need to happen at a minimum? Which can move?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.ENERGY,
        KnowledgeKind.ENERGY,
        (
            (
                "rhythm",
                "When are energy and concentration strongest, and when do you tend to crash?",
            ),
            (
                "focus",
                "How long can you realistically focus, and how much sleep helps you function well?",
            ),
            (
                "drains",
                "What kinds of work drain or energize you? Do you need decompression after work?",
            ),
            (
                "environment",
                "What environments help you focus, and what gets in the way or leads to procrastination?",
            ),
            (
                "checkins",
                "How often would energy check-ins help, and how much should a low-energy day change the plan?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.WELLNESS,
        KnowledgeKind.PREFERENCE,
        (
            (
                "scope",
                "What non-medical wellness support would you like in your plans? We can keep this to sleep, movement, meals, and recovery.",
            ),
            (
                "needs",
                "Are there accessibility needs, appointments, or explicitly requested reminders you want me to account for? You can skip any detail.",
            ),
        ),
    ),
    *_topics(
        LifeDomain.HABITS,
        KnowledgeKind.PREFERENCE,
        (
            ("direction", "Which habits are you trying to build, maintain, reduce, or quit?"),
            (
                "patterns",
                "Which habits happen naturally, which tend to fall apart, and what triggers either pattern?",
            ),
            (
                "pressure",
                "Which streaks matter to you, and which habits should never become guilt-producing metrics?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.LOGISTICS,
        KnowledgeKind.PLACE,
        (
            (
                "destinations",
                "What are your regular destinations and transportation options? General locations are fine.",
            ),
            (
                "constraints",
                "What travel, parking, vehicle, or geographic constraints should I account for?",
            ),
            ("resources", "What equipment, devices, and workspaces are available to you?"),
            (
                "preferences",
                "Are there places you prefer or avoid, or recurring errands to combine?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.PROJECTS,
        KnowledgeKind.PROJECT,
        (
            (
                "inventory",
                "What are all the things you're currently working on? Let's start with an inventory.",
            ),
            (
                "domains",
                "Anything else in work, business, software, school, community, home, relationships, finances, creative work, learning, or administration?",
            ),
            (
                "outcomes",
                "For each project, what outcome do you want, why does it matter, and should it stay active?",
            ),
            (
                "next",
                "For each active project, what's the next action, deadline, blocker, dependency, and who or what do you need?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.OPEN_LOOPS,
        KnowledgeKind.OPEN_LOOP,
        (
            (
                "head",
                "Before we schedule anything: what are you carrying in your head that isn't already captured? I'll put it in your inbox.",
            ),
            (
                "people",
                "Any calls, texts, emails, promises, follow-ups, job applications, interviews, or replies you're waiting for?",
            ),
            (
                "admin",
                "Any appointments to book or cancel, forms, paperwork, bills, subscriptions, purchases, or returns?",
            ),
            (
                "home",
                "Any repairs, vehicle maintenance, chores, errands, cleaning, or things to sell?",
            ),
            (
                "commitments",
                "Any work, school, community, family, or relationship obligations still uncaptured?",
            ),
            (
                "occasions",
                "Any gifts, birthdays, trips, reservations, or wellness appointments to handle?",
            ),
            (
                "thinking",
                "Any research, decisions, documents, software fixes, ideas, or things you keep thinking you need to do?",
            ),
            ("away", "If you disappeared for a week, what would you worry you forgot?"),
            ("avoiding", "What have you been putting off?"),
            ("promises", "What have you told someone you would do?"),
            ("bothering", "What is bothering you because it isn't handled?"),
            (
                "else",
                "What else? Take your time. When nothing else comes to mind, you can say the sweep is complete.",
            ),
        ),
    ),
    *_topics(
        LifeDomain.NOW,
        KnowledgeKind.CURRENT_STATE,
        (
            (
                "changes",
                "What's going on right now? Has anything happened recently that changes your plans?",
            ),
            ("feelings", "What are you worried about, and what are you excited about?"),
            (
                "waiting",
                "What decisions are unresolved, what's blocked, who's waiting on you, and what are you waiting on?",
            ),
            (
                "deadlines",
                "Which deadlines are approaching, and what absolutely needs to happen tomorrow?",
            ),
            ("success", "What would make this week—and the next thirty days—successful?"),
        ),
    ),
    *_topics(
        LifeDomain.PLANNING,
        KnowledgeKind.PREFERENCE,
        (
            (
                "structure",
                "How structured should your days be, and how much unscheduled time feels right?",
            ),
            (
                "buffers",
                "How much buffer, travel, preparation, and decompression should I protect?",
            ),
            ("protected", "What should never move, and how strongly should I protect sleep?"),
            (
                "missed",
                "How should missed tasks be handled? When should work roll forward, and when should I ask?",
            ),
            (
                "focus",
                "How many major priorities per day feels realistic? Do you prefer batching or variety?",
            ),
            ("weekends", "How should weekends differ, and what does free time mean to you?"),
        ),
    ),
    *_topics(
        LifeDomain.AUTONOMY,
        KnowledgeKind.TRUST_PREFERENCE,
        (
            (
                "boundaries",
                "What would you like me to do without asking, and what must always need approval? These preferences won't change permissions by themselves.",
            ),
            (
                "actions",
                "Consider reading, recommending, drafting, proposing, scheduling, rescheduling, sending, buying, deleting, committing, delegating, and computer control. Where are your boundaries?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.COMMUNICATION,
        KnowledgeKind.COMMUNICATION,
        (
            (
                "style",
                "Do you prefer concise or detailed answers, direct advice or exploration, and what tone feels right?",
            ),
            (
                "interruptions",
                "When are interruptions welcome, when should I stay quiet, and how often should I notify you?",
            ),
            (
                "challenge",
                "Should I challenge unrealistic plans? How directly, and with how much explanation?",
            ),
            (
                "briefs",
                "What would useful morning and evening briefs sound like? Do you prefer voice or text?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.STRESS,
        KnowledgeKind.DECISION_PREFERENCE,
        (
            ("support", "When life goes sideways, how should I help?"),
            (
                "tradeoffs",
                "After bad sleep, late work, low energy, missed tasks, or an urgent family issue, what gets dropped first and what stays protected?",
            ),
        ),
    ),
    *_topics(
        LifeDomain.ANTI_GOALS,
        KnowledgeKind.ANTI_GOAL,
        (("boundaries", "What do you not want your life to become?"),),
    ),
    *_topics(
        LifeDomain.TRUST,
        KnowledgeKind.TRUST_PREFERENCE,
        (
            ("trust", "What would make you trust me, and what would make you stop using me?"),
            ("never", "What should I never do, and which decisions should always remain yours?"),
            (
                "challenge",
                "Where do you want me to challenge you, and where should I stay out of the way?",
            ),
        ),
    ),
)


# A focused interview objective reuses canonical topic keys and answer persistence.
WEEK_PLANNING_TOPICS = (
    InterviewTopic(
        "work.role",
        LifeDomain.WORK,
        KnowledgeKind.FACT,
        "What is your current employment situation?",
        staleness_days=7,
    ),
    InterviewTopic(
        "work.schedule",
        LifeDomain.WORK,
        KnowledgeKind.FACT,
        "For the next seven days, which dates will you work, and what are the start and end times? Include your timezone and any commute or preparation time.",
        staleness_days=7,
    ),
    InterviewTopic(
        "planning.fixed_commitments",
        LifeDomain.PLANNING,
        KnowledgeKind.COMMITMENT,
        "What fixed commitments in the next seven days are missing from your digital calendar? Include dates, times, and travel.",
        staleness_days=7,
    ),
    InterviewTopic(
        "values.commitments",
        LifeDomain.VALUES,
        KnowledgeKind.COMMITMENT,
        "Which community or spiritual commitments are current this week, and what are their dates and times?",
        staleness_days=7,
    ),
    *(
        next(t for t in TOPICS if t.key == key)
        for key in (
            "education.program",
            "education.classes",
            "routines.sleep",
            "routines.exercise",
            "routines.meals",
            "projects.inventory",
            "now.success",
        )
    ),
    InterviewTopic(
        "open_loops.known",
        LifeDomain.OPEN_LOOPS,
        KnowledgeKind.OPEN_LOOP,
        "Let's reconcile the tasks already in your inbox. Which are still active, done, or no longer needed? We will clarify them before scheduling.",
    ),
    *(
        next(t for t in TOPICS if t.key == key)
        for key in (
            "open_loops.head",
            "open_loops.promises",
            "open_loops.avoiding",
            "open_loops.bothering",
            "open_loops.away",
            "open_loops.else",
        )
    ),
)
