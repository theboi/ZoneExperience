"""The system-global Friendly Bot seed."""

from __future__ import annotations

from typing import Final

from friendly_bot.seed_types import SystemGlobalSeedDocument

SYSTEM_GLOBAL_SEED: Final[SystemGlobalSeedDocument] = {
    "root": {
        "key": "system.global",
        "trigger": None,
        "actions": [
            {
                "type": "send_message",
                "text": "Hey {{ user.name }}! Nice to meet you! Welcome to The "
                "Zone! I'm Friendly Bot, here to help you get connected "
                "to our wonderful community!",
            },
            {
                "type": "send_buttons",
                "service_bound": False,
                "buttons": [
                    {
                        "button_id": "system.global.menu.timings",
                        "text": "when do we gather?",
                    },
                    {
                        "button_id": "system.global.menu.directions",
                        "text": "how to get to service?",
                    },
                    {
                        "button_id": "system.global.menu.expect",
                        "text": "what to expect?",
                    },
                    {
                        "button_id": "system.global.menu.zone",
                        "text": "what is The Zone?",
                    },
                    {
                        "button_id": "system.global.menu.connect",
                        "text": "get connected!",
                    },
                ],
            },
        ],
        "next_flow_mode": "CHECKPOINT",
        "return_actions": [
            {
                "type": "send_message",
                "text": "Is there anything else I can help you with? "
                "(you can ask me any question!)",
            },
            {
                "type": "send_buttons",
                "service_bound": False,
                "buttons": [
                    {
                        "button_id": "system.global.menu.timings",
                        "text": "when do we gather?",
                    },
                    {
                        "button_id": "system.global.menu.directions",
                        "text": "how to get to service?",
                    },
                    {
                        "button_id": "system.global.menu.expect",
                        "text": "what to expect?",
                    },
                    {
                        "button_id": "system.global.menu.zone",
                        "text": "what is The Zone?",
                    },
                    {
                        "button_id": "system.global.menu.connect",
                        "text": "get connected!",
                    },
                ],
            },
        ],
        "next_flows": [
            {
                "key": "system.global.never_mind",
                "trigger": {
                    "type": "message",
                    "llm_gist": "The person wants to stop, cancel, "
                    "go back, leave the current topic, "
                    "or says never mind.",
                },
                "actions": [{"type": "return_to_nearest_checkpoint"}],
                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.safety",
                "trigger": {
                    "type": "message",
                    "llm_gist": "Select only when the person's "
                    "current message clearly says they "
                    "face immediate physical danger, are "
                    "considering suicide or self-harm, "
                    "are being abused, or urgently need "
                    "a safe responsible adult. Do not "
                    "select this for greetings, ordinary "
                    "questions, general distress, "
                    "ambiguous requests for help, jokes, "
                    "or figurative language.",
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Hey, thank you for telling me... you "
                        "do not have to handle this alone. Let "
                        "me help you find someone to talk "
                        "to...",
                    },
                    {"type": "show_activity", "activity": "typing"},
                    {
                        "type": "find_and_reserve_safety_responder",
                        "service_id": "{{ active_service.id | optional }}",
                        "require_service_attendance_or_always_available": True,
                        "capacity_required": 1,
                    },
                ],
                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                "return_actions": [],
                "next_flows": [
                    {
                        "key": "system.global.safety.responder_found",
                        "trigger": {
                            "type": "action_event",
                            "event_key": "safety_match.found",
                        },
                        "actions": [
                            {
                                "type": "notify_matched_human",
                                "text": "Urgent support request "
                                "from {{ user.name }}. "
                                "Please contact them as "
                                "soon as possible. Only "
                                "information the person "
                                "provided in this flow "
                                "may be included.",
                            },
                            {
                                "type": "share_human_contact",
                                "text": "{{ matched_human.name "
                                "}} is a trusted person "
                                "you can contact now: "
                                "{{ "
                                "matched_human.telegram_url "
                                "}}",
                            },
                        ],
                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                    {
                        "key": "system.global.safety.no_responder",
                        "trigger": {
                            "type": "action_event",
                            "event_key": "safety_match.not_found",
                        },
                        "actions": [
                            {
                                "type": "send_message",
                                "text": "I can't reach a "
                                "trusted person through "
                                "the bot right now. If "
                                "you may be in "
                                "immediate danger, call "
                                "emergency services or "
                                "go to a trusted adult "
                                "near you now.",
                            },
                            {
                                "type": "notify_all_admins",
                                "severity": "urgent",
                                "safe_summary": "A safety "
                                "connection "
                                "request has no "
                                "eligible "
                                "responder.",
                            },
                            {"type": "mark_safety_request_pending"},
                        ],
                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                ],
            },
            {
                "key": "system.global.options",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "what can you help with?",
                        "what are my options?",
                        "show me the menu",
                    ],
                },
                "actions": [{"type": "return_to_nearest_checkpoint"}],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.menu.timings",
                "multi_intent_mode": "interactive",
                "trigger": {
                    "type": "any_of",
                    "triggers": [
                        {"type": "button", "button_id": "system.global.menu.timings"},
                        {
                            "type": "message",
                            "possible_qns": [
                                "when are services?",
                                "when do you all gather?",
                                "what time is youth service?",
                            ],
                        },
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "we have different youth groups for "
                        "different ages! which are you "
                        "referring to?",
                    },
                    {
                        "type": "send_message_fixed",
                        "text": "DARE: for secondary school students "
                        "aged 13-17yo\n"
                        "Arrow: for post-secondary school "
                        "students and NSFs aged 17-23yo\n"
                        "Varsity: for university students",
                    },
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [
                    {
                        "key": "system.global.menu.timings.dare",
                        "multi_intent_mode": "answer",
                        "trigger": {
                            "type": "message",
                            "possible_qns": [
                                "Dare",
                                "when is Dare?",
                                "what time is Dare?",
                            ],
                        },
                        "actions": [
                            {
                                "type": "send_message",
                                "text": "DARE services are held "
                                "on DARE_SERVICE_DAY. "
                                "Doors open at "
                                "DARE_DOORS_OPEN_TIME, "
                                "service starts at "
                                "DARE_SERVICE_START_TIME, "
                                "and ends at "
                                "DARE_SERVICE_END_TIME "
                                "at "
                                "DARE_SERVICE_VENUE.",
                            }
                        ],
                        "next_flow_mode": "ALLOW_MANY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                    {
                        "key": "system.global.menu.timings.arrow",
                        "multi_intent_mode": "answer",
                        "trigger": {
                            "type": "message",
                            "possible_qns": [
                                "Arrow",
                                "when is Arrow?",
                                "what time is Arrow?",
                            ],
                        },
                        "actions": [
                            {
                                "type": "send_message",
                                "text": "Arrow services are "
                                "held on "
                                "ARROW_SERVICE_DAY. "
                                "Doors open at "
                                "ARROW_DOORS_OPEN_TIME, "
                                "service starts at "
                                "ARROW_SERVICE_START_TIME, "
                                "and ends at "
                                "ARROW_SERVICE_END_TIME "
                                "at "
                                "ARROW_SERVICE_VENUE.",
                            }
                        ],
                        "next_flow_mode": "ALLOW_MANY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                    {
                        "key": "system.global.menu.timings.varsity",
                        "multi_intent_mode": "answer",
                        "trigger": {
                            "type": "message",
                            "possible_qns": [
                                "Varsity",
                                "when is Varsity?",
                                "what time is Varsity?",
                            ],
                        },
                        "actions": [
                            {
                                "type": "send_message",
                                "text": "Varsity services are "
                                "held on "
                                "VARSITY_SERVICE_DAY. "
                                "Doors open at "
                                "VARSITY_DOORS_OPEN_TIME, "
                                "service starts at "
                                "VARSITY_SERVICE_START_TIME, "
                                "and ends at "
                                "VARSITY_SERVICE_END_TIME "
                                "at "
                                "VARSITY_SERVICE_VENUE.",
                            }
                        ],
                        "next_flow_mode": "ALLOW_MANY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                    {
                        "key": "system.global.menu.timings.other_service",
                        "multi_intent_mode": "answer",
                        "trigger": {
                            "type": "message",
                            "llm_gist": "The person answers "
                            "the current "
                            "youth-group timing "
                            "question with a "
                            "group other than "
                            "Dare, Arrow, or "
                            "Varsity.",
                        },
                        "actions": [
                            {
                                "type": "send_message",
                                "text": "I have timing details "
                                "for Dare, Arrow, and "
                                "Varsity. Which of "
                                "these groups did you "
                                "mean?",
                            }
                        ],
                        "next_flow_mode": "ALLOW_MANY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                ],
            },
            {
                "key": "system.global.menu.directions",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "any_of",
                    "triggers": [
                        {
                            "type": "button",
                            "button_id": "system.global.menu.directions",
                        },
                        {
                            "type": "message",
                            "possible_qns": [
                                "how do i get to The Zone?",
                                "where is The Zone?",
                                "how do i get to Star Vista?",
                            ],
                        },
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Our services are held at Star Vista! "
                        "Take the MRT to Buona Vista and follow "
                        "the signs!",
                    },
                    {
                        "type": "send_message_fixed",
                        "text": "1 Vista Exchange Green, Singapore "
                        "138617\n"
                        "https://maps.google.com/?q=The+Star+Performing+Arts+Centre",
                    },
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.menu.expect",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "any_of",
                    "triggers": [
                        {"type": "button", "button_id": "system.global.menu.expect"},
                        {
                            "type": "message",
                            "possible_qns": [
                                "what should i expect?",
                                "what happens at The Zone?",
                                "can i come alone?",
                            ],
                        },
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Come as you are. You can expect music, "
                        "a message about Jesus, and time to "
                        "meet other youths. It's okay to come "
                        "alone, sit quietly, or ask for someone "
                        "to meet you before you enter.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.menu.connect",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "any_of",
                    "triggers": [
                        {"type": "button", "button_id": "system.global.menu.connect"},
                        {
                            "type": "message",
                            "possible_qns": [
                                "how do i get connected?",
                                "can i get the connect link?",
                            ],
                        },
                    ],
                },
                "actions": [
                    {
                        "type": "send_message_fixed",
                        "text": "Get connected at "
                        "https://bit.ly//thezonenew! We would "
                        "love to hear from you!",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.schedule.dare",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["when is Dare?", "what time is Dare service?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Dare services are held on "
                        "DARE_SERVICE_DAY. Doors open at "
                        "DARE_DOORS_OPEN_TIME, service starts "
                        "at DARE_SERVICE_START_TIME, and ends "
                        "at DARE_SERVICE_END_TIME at "
                        "DARE_SERVICE_VENUE.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.schedule.arrow",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["when is Arrow?", "what time is Arrow service?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Arrow services are held on "
                        "ARROW_SERVICE_DAY. Doors open at "
                        "ARROW_DOORS_OPEN_TIME, service starts "
                        "at ARROW_SERVICE_START_TIME, and ends "
                        "at ARROW_SERVICE_END_TIME at "
                        "ARROW_SERVICE_VENUE.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.schedule.varsity",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "when is Varsity?",
                        "what time is Varsity service?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Varsity services are held on "
                        "VARSITY_SERVICE_DAY. Doors open at "
                        "VARSITY_DOORS_OPEN_TIME, service "
                        "starts at VARSITY_SERVICE_START_TIME, "
                        "and ends at VARSITY_SERVICE_END_TIME "
                        "at VARSITY_SERVICE_VENUE.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.schedule.next_gathering",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "is there youth service today?",
                        "when is the next gathering?",
                        "is there service this week?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "The next gathering is "
                        "NEXT_GATHERING_DATE at "
                        "NEXT_GATHERING_TIME. Please add the "
                        "current event calendar or link: "
                        "UPCOMING_EVENTS_LINK.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.schedule.duration",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "how long is service?",
                        "what time does youth service end?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "A typical service lasts "
                        "SERVICE_DURATION. Please update this "
                        "if it differs for the service you "
                        "mean.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.schedule.status",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "is service still happening?",
                        "has service been cancelled?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please check OFFICIAL_UPDATES_LINK for "
                        "the latest service updates, or contact "
                        "OFFICIAL_UPDATES_CONTACT.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.about.ncc",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "what is NCC?",
                        "what does NCC stand for?",
                        "is The Zone part of a church?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "NCC is New Creation Church. Please add "
                        "a short approved description here: "
                        "NCC_DESCRIPTION.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.about.zone",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "any_of",
                    "triggers": [
                        {"type": "button", "button_id": "system.global.menu.zone"},
                        {
                            "type": "message",
                            "possible_qns": [
                                "what is The Zone?",
                                "what do you mean by The Zone?",
                                "is The Zone part of a church?",
                            ],
                        },
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "The Zone is New Creation Church's "
                        "energy-packed youth ministry, and "
                        "reaches out to all secondary and "
                        "tertiary students as well as full-time "
                        "national servicemen in community and "
                        "in motion for the grace revolution. "
                        "Centred on the foundation of the Word "
                        "of God, the ministry's call is wrapped "
                        "up in the message of God's unmerited, "
                        "undeserved favour! The Zone is the "
                        "place for building godly relationships "
                        "and growing in revelation of God's "
                        "grace.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.about.dare",
                "multi_intent_mode": "answer",
                "trigger": {"type": "message", "possible_qns": ["what is DARE?"]},
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Growing up and trying to stay afloat "
                        "amidst endless homework and "
                        "responsibilities? Come face to face "
                        "with the One who wants to calm the "
                        "storms in your life and be the anchor "
                        "of your soul. DARE is a place where "
                        "you will discover your purpose and "
                        "meet authentic friends who will never "
                        "let you walk alone. #DAREishome (for "
                        "secondary school students aged "
                        "13-17yo)",
                    },
                    {
                        "type": "send_message",
                        "text": "Follow us at @nccdare on Instagram for "
                        "latest updates on service dates and "
                        "timings.",
                    },
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.about.arrow",
                "multi_intent_mode": "answer",
                "trigger": {"type": "message", "possible_qns": ["what is Arrow?"]},
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Ok, you're in a new season and the "
                        "world's your oyster. Wondering what "
                        "the future holds? We know that God "
                        "will use this season to prepare and "
                        "set you up for all the plans and the "
                        "purposes He has for you. Because "
                        "#ArrowIsFamily—you don't have to act, "
                        "dress or talk in a certain way to "
                        "belong. You can come as you are. We "
                        "believe that the message of Jesus will "
                        "radically transform your life. Come "
                        "and discover His perfect love for "
                        "you.  (for post-secondary school "
                        "students and NSFs aged 17-23yo)",
                    },
                    {
                        "type": "send_message",
                        "text": "Follow us at @nccarrow on Instagram "
                        "for latest updates on service dates "
                        "and timings.",
                    },
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.about.varsity",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["what is V / Varsity?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Whether you've got a packed semester, "
                        "an intense elective, or a chill "
                        "internship—your university experience "
                        "is shaped by the people you're "
                        "surrounded with. At V, we are "
                        "committed to taking this journey "
                        "together with unstoppable faith and "
                        "irresistible wisdom. We crave intimate "
                        "and real relationships with Jesus and "
                        "with each other. We are a fam that "
                        "will never let you walk through life "
                        "alone. (for university students)",
                    },
                    {
                        "type": "send_message",
                        "text": "Follow us at @nccvarsity on Instagram "
                        "for latest updates on service dates "
                        "and timings.",
                    },
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.about.eligibility",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "who is The Zone for?",
                        "am i too old for The Zone?",
                        (
                            "can primary school/secondary school/junior "
                            "college/JC/Poly/Polytechnic students come?"
                        ),
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "The Zone consists of three youth "
                        "groups designed for students and NSF "
                        "aged 13-25yo. If you are a working "
                        "adult, you can join our English care "
                        "groups and find support for the season "
                        "you are in!",
                    },
                    {
                        "type": "send_message_fixed",
                        "text": "DARE: for secondary school students "
                        "aged 13-17yo\n"
                        "Arrow: for post-secondary school "
                        "students and NSFs aged 17-23yo\n"
                        "Varsity: for university students",
                    },
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.about.not_christian",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "can i come if i am not Christian?",
                        "can i come even if i am not sure about faith?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "You are welcome at The Zone whether or "
                        "not you are Christian. You can come, "
                        "ask questions, and take things at your "
                        "own pace.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.about.purpose",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "why should i come to The Zone?",
                        "what is the point of The Zone?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "The Zone is a place to meet people, "
                        "explore faith, and grow in community. "
                        "Please add the approved purpose "
                        "statement here: THE_ZONE_PURPOSE.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.about.cost",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "does The Zone cost money?",
                        "do i need to pay for service?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "The Zone costs COST_OR_FREE_DETAILS. "
                        "Please add any event-specific price, "
                        "payment, or financial-help details "
                        "here.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.arrival.first_visit",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "what do i do when i arrive for the first time?",
                        "where do i go for check-in?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "When you arrive, go to "
                        "FIRST_TIME_WELCOME_POINT and look for "
                        "FIRST_TIME_TEAM_DESCRIPTION. Please "
                        "add the check-in details here.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.arrival.registration",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "do i need to register?",
                        "do i need to buy a ticket?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "REGISTRATION_REQUIREMENT. Please add "
                        "the registration link or instructions "
                        "here: REGISTRATION_LINK.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.arrival.guest",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["can i bring a friend?", "can my sibling come?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "You are welcome to bring a friend. "
                        "Please add any guest, sibling, parent, "
                        "or guardian requirements here: "
                        "GUEST_POLICY.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.arrival.late",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "can i still come if i am late?",
                        "what if service has already started?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "You can still come. Please add the "
                        "late-arrival instructions, entrance, "
                        "and service-specific limits here: "
                        "LATE_ARRIVAL_INSTRUCTIONS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.travel.bus",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "how do i get to The Zone by bus?",
                        "which bus goes to Star Vista?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the recommended bus "
                        "services and stop here: "
                        "BUS_DIRECTIONS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.travel.drive",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "where can i park?",
                        "where should my Grab drop me off?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the parking, drop-off, "
                        "cost, and ride-hailing details here: "
                        "PARKING_AND_DROPOFF_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.travel.entrance",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "which entrance should i use?",
                        "what level is The Zone on?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the entrance, level, room, "
                        "and meeting-point details here: "
                        "VENUE_ARRIVAL_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.travel.accessibility",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "is the venue wheelchair accessible?",
                        "is there a lift?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the venue accessibility "
                        "arrangements and contact here: "
                        "ACCESSIBILITY_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.expect.programme",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "what happens during service?",
                        "what is the programme like?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "A typical gathering includes music, a "
                        "message about Jesus, and time to meet "
                        "other youths. Please add the approved "
                        "programme order and duration here: "
                        "TYPICAL_PROGRAMME_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.expect.language",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "what language is service in?",
                        "is there translation?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Services are in SERVICE_LANGUAGE. "
                        "Please add interpretation or "
                        "translation support here: "
                        "INTERPRETATION_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.expect.sensory",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["will it be loud?", "is there a quiet space?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the sound, lighting, "
                        "quiet-space, and sensory-support "
                        "details here: "
                        "SENSORY_SUPPORT_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.expect.participation",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["will i have to speak?", "do i have to join in?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "You can take things at your own pace. "
                        "Please add the approved participation "
                        "and privacy reassurance here: "
                        "PARTICIPATION_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.community.meet_someone",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "can i talk to a real person?",
                        "can i meet someone from The Zone?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the approved contact path "
                        "for meeting someone outside a live "
                        "service here: "
                        "COMMUNITY_CONTACT_PATH.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.community.socials",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "what is The Zone instagram?",
                        "do you have a Telegram channel?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the official social links "
                        "here: INSTAGRAM_LINK, "
                        "TELEGRAM_CHANNEL_LINK, WEBSITE_LINK.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.community.serve",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["how can i volunteer?", "can i join a team?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the serving process, age "
                        "requirements, and contact here: "
                        "SERVING_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.community.contact_after",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "who can i contact after service?",
                        "can i talk to someone later this week?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the approved follow-up "
                        "contact here: FOLLOW_UP_CONTACT.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.faith.jesus",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "who is Jesus?",
                        "what do Christians believe about Jesus?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "At NCC, we believe Jesus is the Son of "
                        "God who came to reveal God's love and "
                        "give us new life through His death and "
                        "resurrection.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.faith.christianity",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "what is Christianity?",
                        "what do Christians believe?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add a short approved "
                        "explanation of Christian belief here: "
                        "CHRISTIANITY_EXPLANATION.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.faith.follow_jesus",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "how do i become a Christian?",
                        "how do i follow Jesus?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the approved next-steps "
                        "explanation and contact here: "
                        "FOLLOW_JESUS_NEXT_STEPS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.faith.bible_baptism",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "how do i start reading the Bible?",
                        "what is baptism?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the approved Bible, "
                        "baptism, and discipleship next steps "
                        "here: FAITH_NEXT_STEPS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.faith.prayer",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["can you pray for me?", "how do i pray?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the approved prayer "
                        "response and contact path here: "
                        "PRAYER_SUPPORT_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.faith.personal_question",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "can i ask someone a private faith question?",
                        "can i talk to someone about faith?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the approved private "
                        "faith-conversation contact path here: "
                        "FAITH_CONVERSATION_CONTACT.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.support.personal",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "i feel really lonely",
                        "can i talk to someone about something personal?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Thank you for sharing that. Please add "
                        "the approved non-emergency support "
                        "contact and wording here: "
                        "WELLBEING_SUPPORT_CONTACT.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.venue.toilet",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["where is the toilet?", "where is the restroom?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "The nearest toilets are on Level 4 "
                        "beside the lifts. Ask a Zone team "
                        "member if you would like someone to "
                        "show you.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.venue.food_water",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["is there food?", "where can i get water?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add food, drink, water, "
                        "refreshment, and cost details here: "
                        "FOOD_AND_WATER_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.venue.wifi_charging",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["is there wifi?", "can i charge my phone?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the Wi-Fi and charging "
                        "policy here: "
                        "WIFI_AND_CHARGING_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.venue.lost_property",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["i lost something", "where is lost and found?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the lost-and-found location "
                        "and contact here: "
                        "LOST_PROPERTY_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.venue.medical",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "i do not feel well",
                        "where can i get first aid?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the on-site medical-help "
                        "and team-member instructions here: "
                        "ON_SITE_HELP_INSTRUCTIONS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.policy.privacy",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "is my information private?",
                        "what do you do with my details?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the approved privacy "
                        "explanation and policy link here: "
                        "PRIVACY_POLICY_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.policy.photos",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": ["are photos taken?", "can i opt out of photos?"],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the approved photo, video, "
                        "and opt-out policy here: "
                        "MEDIA_POLICY_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.policy.parental_consent",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "do i need parental consent?",
                        "can my parent come with me?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the parental-consent and "
                        "guardian policy here: "
                        "PARENTAL_CONSENT_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.policy.safeguarding",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "what are the behaviour rules?",
                        "how do you keep people safe?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "Please add the approved safeguarding, "
                        "reporting, and behaviour-policy "
                        "details here: SAFEGUARDING_DETAILS.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.help.unanswered",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "llm_gist": "The person asks a genuine question "
                    "about The Zone, NCC, a youth "
                    "service, or attending that is not "
                    "covered by a more specific "
                    "available question flow.",
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "I do not have an approved answer for "
                        "that yet. Please add the best contact "
                        "for unanswered questions here: "
                        "GENERAL_QUESTION_CONTACT.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.community.small_group",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "how do i join a small group?",
                        "do you have cell groups?",
                    ],
                },
                "actions": [
                    {
                        "type": "send_message",
                        "text": "We would love to help you find a "
                        "group. What is your age or school "
                        "stage, and what area are you usually "
                        "in?",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [
                    {
                        "key": "system.global.community.small_group.follow_up",
                        "multi_intent_mode": "answer",
                        "trigger": {
                            "type": "message",
                            "llm_gist": "The person answers "
                            "the current "
                            "small-group "
                            "question with their "
                            "age, school stage, "
                            "area, availability, "
                            "or group "
                            "preference.",
                        },
                        "actions": [
                            {
                                "type": "send_message",
                                "text": "Thanks for sharing. "
                                "Please add the "
                                "approved small-group "
                                "follow-up contact or "
                                "form here: "
                                "SMALL_GROUP_CONTACT_OR_LINK.",
                            }
                        ],
                        "next_flow_mode": "ALLOW_MANY",
                        "return_actions": [],
                        "next_flows": [],
                    }
                ],
            },
        ],
    }
}
