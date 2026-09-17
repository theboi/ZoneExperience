"""The system-global Friendly Bot seed."""

from __future__ import annotations

from typing import Final

from friendly_bot.seed_types import SeedValue, SystemGlobalSeedDocument

MAIN_MENU_BUTTONS: Final[SeedValue] = [
    { "button_id": "system.global.menu.timings", "text": "when do we gather?" },
    { "button_id": "system.global.menu.directions", "text": "how to get to service?" },
    { "button_id": "system.global.menu.expect", "text": "what to expect?" },
    { "button_id": "system.global.menu.zone", "text": "what is The Zone?" },
    { "button_id": "system.global.menu.connect", "text": "get connected!" }
]

SYSTEM_GLOBAL_SEED: Final[SystemGlobalSeedDocument] = {
    "operational_profiles": [
        {
            "name": "ryan the",
            "dob": "1990-01-01",
            "role": "leader",
            "interests": [],
            "cg_name": None,
            "telegram_contact_url": None,
            "always_available": False,
            "capacity": 1,
            "is_admin": True,
        },
    ],
    "root": {
        "key": "system.global",
        "trigger": None,
        "actions": [
            {
                "type": "send_message_paraphrased",
                "text": "Hey {{ user.name }}! Nice to meet you! Welcome to The Zone! I'm Friendly Bot, here to help you get connected to our wonderful community!",
            },
            {
                "type": "send_buttons",
                "service_bound": False,
                "buttons": MAIN_MENU_BUTTONS
            },
        ],
        "next_flow_mode": "CHECKPOINT",
        "return_actions": [
            {
                "type": "send_message_paraphrased",
                "text": "Is there anything else I can help you with? You can ask me anything and I will try my best to answer you!",
            },
            {
                "type": "send_buttons",
                "service_bound": False,
                "buttons": MAIN_MENU_BUTTONS
            },
        ],
        "next_flows": [
            ##### ADMINISTRATIVE #####
            {
                "key": "system.global.never_mind",
                "trigger": {
                    "type": "message",
                    "llm_gist": "The person wants to stop, cancel, go back, leave the current topic, or says never mind.",
                },
                "actions": [{"type": "return_to_nearest_checkpoint"}],
                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.operational.login",
                "trigger": {"type": "command", "command": "/login"},
                "actions": [
                    {
                        "type": "send_message_paraphrased",
                        "text": "hey! please reply with the name on your server or leader profile.",
                    }
                ],
                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                "return_actions": [],
                "next_flows": [
                    {
                        "key": "system.global.operational.login.name",
                        "trigger": {"type": "any_message"},
                        "actions": [{"type": "capture_operational_login_name"}],
                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                        "return_actions": [],
                        "next_flows": [
                            {
                                "key": "system.global.operational.login.name.captured",
                                "trigger": {"type": "action_event", "event_key": "operational_login.name_captured"},
                                "actions": [
                                    {
                                        "type": "send_message_paraphrased",
                                        "text": "thanks! now reply with your date of birth in DD/MM/YYYY format.",
                                    }
                                ],
                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                "return_actions": [],
                                "next_flows": [
                                    {
                                        "key": "system.global.operational.login.dob",
                                        "trigger": {"type": "any_message"},
                                        "actions": [{"type": "complete_operational_login"}],
                                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                        "return_actions": [],
                                        "next_flows": [
                                            {
                                                "key": "system.global.operational.login.attached",
                                                "trigger": {"type": "action_event", "event_key": "operational_login.attached"},
                                                "actions": [
                                                    {
                                                        "type": "send_message_paraphrased",
                                                        "text": "you are logged in! use /manage whenever you want to update your interests, or /logout when you are done.",
                                                    }
                                                ],
                                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                "return_actions": [],
                                                "next_flows": [],
                                            },
                                            {
                                                "key": "system.global.operational.login.interests_required",
                                                "trigger": {"type": "action_event", "event_key": "operational_login.interests_required"},
                                                "actions": [
                                                    {
                                                        "type": "send_message_paraphrased",
                                                        "text": "you are logged in! tell me one or more interests or conversation topics, separated by commas, so i can help make good connections.",
                                                    }
                                                ],
                                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                "return_actions": [],
                                                "next_flows": [
                                                    {
                                                        "key": "system.global.operational.login.interests",
                                                        "trigger": {"type": "any_message"},
                                                        "actions": [{"type": "save_operational_interests"}],
                                                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                        "return_actions": [],
                                                        "next_flows": [
                                                            {
                                                                "key": "system.global.operational.login.interests.saved",
                                                                "trigger": {"type": "action_event", "event_key": "operational_interests.saved"},
                                                                "actions": [
                                                                    {
                                                                        "type": "send_message_paraphrased",
                                                                        "text": "saved! your interests are ready to use for matching. use /manage to change them or /logout when you are done.",
                                                                    }
                                                                ],
                                                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                                "return_actions": [],
                                                                "next_flows": [],
                                                            },
                                                            {
                                                                "key": "system.global.operational.login.interests.invalid",
                                                                "trigger": {"type": "action_event", "event_key": "operational_interests.invalid"},
                                                                "actions": [
                                                                    {
                                                                        "type": "send_message_paraphrased",
                                                                        "text": "please send between one and ten short interests, separated by commas. start /login again when you are ready.",
                                                                    }
                                                                ],
                                                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                                "return_actions": [],
                                                                "next_flows": [],
                                                            },
                                                            {
                                                                "key": "system.global.operational.login.interests.not_attached",
                                                                "trigger": {"type": "action_event", "event_key": "operational_interests.not_attached"},
                                                                "actions": [
                                                                    {
                                                                        "type": "send_message_paraphrased",
                                                                        "text": "your login is no longer active, so your interests were not changed. please start /login again.",
                                                                    }
                                                                ],
                                                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                                "return_actions": [],
                                                                "next_flows": [],
                                                            },
                                                        ],
                                                    }
                                                ],
                                            },
                                            {
                                                "key": "system.global.operational.login.occupied",
                                                "trigger": {"type": "action_event", "event_key": "operational_login.occupied"},
                                                "actions": [
                                                    {
                                                        "type": "send_message_paraphrased",
                                                        "text": "i could not log you in because that profile is currently unavailable. please check with a leader if you need help.",
                                                    }
                                                ],
                                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                "return_actions": [],
                                                "next_flows": [],
                                            },
                                            {
                                                "key": "system.global.operational.login.not_found",
                                                "trigger": {"type": "action_event", "event_key": "operational_login.not_found"},
                                                "actions": [
                                                    {
                                                        "type": "send_message_paraphrased",
                                                        "text": "i could not verify those login details. please check them and start /login again.",
                                                    }
                                                ],
                                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                "return_actions": [],
                                                "next_flows": [],
                                            },
                                            {
                                                "key": "system.global.operational.login.not_started",
                                                "trigger": {"type": "action_event", "event_key": "operational_login.not_started"},
                                                "actions": [
                                                    {
                                                        "type": "send_message_paraphrased",
                                                        "text": "your login request expired. please start /login again.",
                                                    }
                                                ],
                                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                "return_actions": [],
                                                "next_flows": [],
                                            },
                                            {
                                                "key": "system.global.operational.login.dob_invalid",
                                                "trigger": {"type": "action_event", "event_key": "operational_login.dob_invalid"},
                                                "actions": [
                                                    {
                                                        "type": "send_message_paraphrased",
                                                        "text": "please use a real date in DD/MM/YYYY format and start /login again.",
                                                    }
                                                ],
                                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                                "return_actions": [],
                                                "next_flows": [],
                                            },
                                        ],
                                    }
                                ],
                            },
                            {
                                "key": "system.global.operational.login.name.invalid",
                                "trigger": {"type": "action_event", "event_key": "operational_login.name_invalid"},
                                "actions": [
                                    {
                                        "type": "send_message_paraphrased",
                                        "text": "please send the name on your profile and start /login again.",
                                    }
                                ],
                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                "return_actions": [],
                                "next_flows": [],
                            },
                        ],
                    }
                ],
            },
            {
                "key": "system.global.operational.manage",
                "trigger": {"type": "command", "command": "/manage"},
                "actions": [{"type": "manage_operational_account"}],
                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                "return_actions": [],
                "next_flows": [
                    {
                        "key": "system.global.operational.manage.editor",
                        "trigger": {"type": "action_event", "event_key": "operational_manage.editor"},
                        "actions": [
                            {
                                "type": "send_message_paraphrased",
                                "text": "send your updated interests or conversation topics, separated by commas.",
                            }
                        ],
                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                        "return_actions": [],
                        "next_flows": [
                            {
                                "key": "system.global.operational.manage.interests",
                                "trigger": {"type": "any_message"},
                                "actions": [{"type": "save_operational_interests"}],
                                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                "return_actions": [],
                                "next_flows": [
                                    {
                                        "key": "system.global.operational.manage.interests.saved",
                                        "trigger": {"type": "action_event", "event_key": "operational_interests.saved"},
                                        "actions": [
                                            {
                                                "type": "send_message_paraphrased",
                                                "text": "saved! use /manage whenever you want to change them again.",
                                            }
                                        ],
                                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                        "return_actions": [],
                                        "next_flows": [],
                                    },
                                    {
                                        "key": "system.global.operational.manage.interests.invalid",
                                        "trigger": {"type": "action_event", "event_key": "operational_interests.invalid"},
                                        "actions": [
                                            {
                                                "type": "send_message_paraphrased",
                                                "text": "please send between one and ten short interests, separated by commas. use /manage to try again.",
                                            }
                                        ],
                                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                        "return_actions": [],
                                        "next_flows": [],
                                    },
                                    {
                                        "key": "system.global.operational.manage.interests.not_attached",
                                        "trigger": {"type": "action_event", "event_key": "operational_interests.not_attached"},
                                        "actions": [
                                            {
                                                "type": "send_message_paraphrased",
                                                "text": "your login is no longer active, so your interests were not changed. please start /login again.",
                                            }
                                        ],
                                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                                        "return_actions": [],
                                        "next_flows": [],
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "key": "system.global.operational.manage.not_attached",
                        "trigger": {"type": "action_event", "event_key": "operational_manage.not_attached"},
                        "actions": [
                            {
                                "type": "send_message_paraphrased",
                                "text": "you are not logged in as a server or leader. start /login first.",
                            }
                        ],
                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                ],
            },
            {
                "key": "system.global.operational.logout",
                "trigger": {"type": "command", "command": "/logout"},
                "actions": [{"type": "logout_operational_account"}],
                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                "return_actions": [],
                "next_flows": [
                    {
                        "key": "system.global.operational.logout.detached",
                        "trigger": {"type": "action_event", "event_key": "operational_logout.detached"},
                        "actions": [
                            {
                                "type": "send_message_paraphrased",
                                "text": "you are logged out. your profile and interests are still saved for next time.",
                            }
                        ],
                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                    {
                        "key": "system.global.operational.logout.not_attached",
                        "trigger": {"type": "action_event", "event_key": "operational_logout.not_attached"},
                        "actions": [
                            {
                                "type": "send_message_paraphrased",
                                "text": "you are not currently logged in as a server or leader.",
                            }
                        ],
                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                ],
            },
            {
                "key": "system.global.thank_you",
                "trigger": {
                    "type": "message",
                    "llm_gist": "The person thanks you.",
                },
                "actions": [{"type": "send_message_paraphrased", "text": "You're welcome!"}],
                "next_flow_mode": "ONE_AND_ONCE_ONLY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.possible_options",
                "trigger": {
                    "type": "message",
                    "possible_qns": [
                        "what can you do/help with?",
                        "what are my options?",
                        "show me the menu",
                    ],
                },
                "actions": [{"type": "send_message_paraphrased", "text": "You can ask me anything and I will try my best to answer you!"}],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            ##### SAFETY #####
            {
                "key": "system.global.safety",
                "trigger": {
                    "type": "message",
                    "llm_gist": "Select only when the person's current message clearly says they face immediate physical danger, are considering suicide or self-harm, are being abused, or urgently need a safe responsible adult. Do not select this for greetings, ordinary questions, general distress, ambiguous requests for help, jokes, or figurative language.",
                },
                "actions": [
                    {
                        "type": "send_message_paraphrased",
                        "text": "Hey, thank you for telling me... you do not have to handle this alone. Let me help you find someone to talk to...",
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
                                "text": "Urgent support request from {{ user.name }}. Please contact them as soon as possible. Only information the person provided in this flow may be included.",
                            },
                            {
                                "type": "share_human_contact",
                                "text": "{{ matched_human.name }} is a trusted person you can contact now: {{ matched_human.telegram_url }}",
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
                                "type": "send_message_paraphrased",
                                "text": "I can't reach a trusted person through the bot right now. If you may be in immediate danger, call emergency services or go to a trusted adult near you now.",
                            },
                            {
                                "type": "notify_all_admins",
                                "severity": "urgent",
                                "safe_summary": "A safety connection request has no eligible responder.",
                            },
                            {"type": "mark_safety_request_pending"},
                        ],
                        "next_flow_mode": "ONE_AND_ONCE_ONLY",
                        "return_actions": [],
                        "next_flows": [],
                    },
                ],
            },
            ##### INFORMATION DESK #####
            {
                "key": "system.global.information.ncc",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "llm_gist": "The person asks about anything related to New Creation Church (NCC) that is not specifically about its youth ministry, The Zone.",
                },
                "actions": [
                    {
                        "type": "send_message_llm",
                        "source": "At New Creation Church, we believe we are God's beloved. He demonstrated this by freely giving up heaven's best, His only Son Jesus, for you and me. When we catch a revelation of this truth, we are transformed by His grace from the inside out. That's the beauty of believing and living in our heavenly Father's love and grace! No matter who you are or where you come from, there's always a place for you in our church family!",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.information.zone",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "any_of",
                    "triggers": [
                        {"type": "button", "button_id": "system.global.menu.timings"},
                        {"type": "button", "button_id": "system.global.menu.zone"},
                        {
                            "type": "message",
                            "llm_gist": "The person asks about anything related to The Zone, or one of its youth groups DARE, Arrow or Varsity/V.",
                        },
                    ],
                },
                "actions": [
                    {
                        "type": "send_message_llm",
                        "source": "The Zone is New Creation Church's energy-packed youth ministry. It reaches out to all secondary and tertiary students as well as NSFs in community and in motion for the grace revolution. Centred on the foundation of the Word of God, the ministry's call is the message of God's unmerited, undeserved favour. The Zone is a place for building godly relationships and growing in revelation of God's grace. The Zone is also a place to meet people, explore faith, and grow in community. Its additional approved purpose statement is THE_ZONE_PURPOSE. The Zone has three youth groups for students and NSFs aged 13-25: DARE is for secondary school students aged 13-17; Arrow is for post-secondary school students and NSFs aged 17-23; Varsity, also called V, is for university students. If someone asks for service times without naming a group, tell them that there are DARE, Arrow, and Varsity services with distinct schedules and ask which group they mean. If a message only names DARE, Arrow, Varsity, or V, give that group's schedule. DARE is a place to discover purpose and meet authentic friends who will never let people walk alone. #DAREishome. DARE's Instagram is @nccdare. DARE services are on DARE_SERVICE_DAY. Doors open at DARE_DOORS_OPEN_TIME, service starts at DARE_SERVICE_START_TIME, and ends at DARE_SERVICE_END_TIME at DARE_SERVICE_VENUE. Arrow is for people in a new season; people can come as they are and discover Jesus' perfect love. #ArrowIsFamily. Arrow's Instagram is @nccarrow. Arrow services are on ARROW_SERVICE_DAY. Doors open at ARROW_DOORS_OPEN_TIME, service starts at ARROW_SERVICE_START_TIME, and ends at ARROW_SERVICE_END_TIME at ARROW_SERVICE_VENUE. Varsity, or V, is a community for university students that values relationships with Jesus and each other. Its Instagram is @nccvarsity. Varsity services are on VARSITY_SERVICE_DAY. Doors open at VARSITY_DOORS_OPEN_TIME, service starts at VARSITY_SERVICE_START_TIME, and ends at VARSITY_SERVICE_END_TIME at VARSITY_SERVICE_VENUE. The next gathering is NEXT_GATHERING_DATE at NEXT_GATHERING_TIME. Its event calendar or link is UPCOMING_EVENTS_LINK. A typical service lasts SERVICE_DURATION; this may differ for the service someone means. For the latest cancellation or service-status updates, use OFFICIAL_UPDATES_LINK or contact OFFICIAL_UPDATES_CONTACT. People are welcome at The Zone whether or not they are Christian; they can ask questions and take things at their own pace. The Zone costs COST_OR_FREE_DETAILS. Event-specific price, payment, or financial-help details still need to be added.",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.information.zone.timings",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "any_of",
                    "triggers": [
                        {"type": "button", "button_id": "system.global.menu.timings"},
                        {
                            "type": "message",
                            "llm_gist": "The person asks about anything related to The Zone, or one of its youth groups DARE, Arrow or Varsity/V.",
                        },
                    ],
                },
                "actions": [
                    {
                        "type": "send_message_fixed",
                        "text": "DARE: for secondary school students aged 13-17yo\nArrow: for post-secondary school students and NSFs aged 17-23yo\nVarsity: for university students",
                    }
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            {
                "key": "system.global.information.directions.star",
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
                            "llm_gist": "The person is lost or asks for directions to/within Star Vista/church"
                        },
                    ],
                },
                "actions": [
                    {
                        "type": "send_message_llm",
                        "source": "Our services are held at Star Vista! Take the MRT to Buona Vista and follow the signs! Once you've reached Star Vista, head to Level 5! (the lift only brings you to Level 3, then you need to take the escalators). Wheelchair assistance is available upon request.",
                    },
                    {
                        "type": "send_message_fixed",
                        "text": "1 Vista Exchange Green, Singapore 138617\nhttps://maps.google.com/?q=The+Star+Performing+Arts+Centre",
                    },
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [],
            },
            # {
            #     "key": "system.global.information.directions.within_star",
            #     "multi_intent_mode": "answer",
            #     "trigger": {
            #         "type": "message",
            #         "llm_gist": "The person asks for directions within Star Vista/church"
            #     },
            #     "actions": [
            #         {
            #             "type": "send_message_paraphrased",
            #             "text": "",
            #         },
            #     ],
            #     "next_flow_mode": "ALLOW_MANY",
            #     "return_actions": [],
            #     "next_flows": [],
            # },
            {
                "key": "system.global.information.handicap_assistance",
                "multi_intent_mode": "answer",
                "trigger": {
                    "type": "message",
                    "llm_gist": "The person asks about/for accessibility/handicap assistance."
                },
                "actions": [
                    {
                        "type": "send_message_paraphrased",
                        "text": "If you need help getting to service, let me know again to confirm and I will get you in contact with someone who can help!",
                    },
                ],
                "next_flow_mode": "ALLOW_MANY",
                "return_actions": [],
                "next_flows": [
                    # {
                    #     "key": "system.global.information.handicap_assistance.confirm",
                    # }
                ],
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
                        "type": "send_message_paraphrased",
                        "text": "Come as you are. You can expect music, a message about Jesus, and time to meet other youths. It's okay to come alone, sit quietly, or ask for someone to meet you before you enter.",
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
                        "text": "Get connected at https://bit.ly//thezonenew! We would love to hear from you!",
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
                        "type": "send_message_paraphrased",
                        "text": "When you arrive, go to FIRST_TIME_WELCOME_POINT and look for FIRST_TIME_TEAM_DESCRIPTION. Please add the check-in details here.",
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
                        "type": "send_message_paraphrased",
                        "text": "REGISTRATION_REQUIREMENT. Please add the registration link or instructions here: REGISTRATION_LINK.",
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
                        "type": "send_message_paraphrased",
                        "text": "You are welcome to bring a friend. Please add any guest, sibling, parent, or guardian requirements here: GUEST_POLICY.",
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
                        "type": "send_message_paraphrased",
                        "text": "You can still come. Please add the late-arrival instructions, entrance, and service-specific limits here: LATE_ARRIVAL_INSTRUCTIONS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the recommended bus services and stop here: BUS_DIRECTIONS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the parking, drop-off, cost, and ride-hailing details here: PARKING_AND_DROPOFF_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the entrance, level, room, and meeting-point details here: VENUE_ARRIVAL_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the venue accessibility arrangements and contact here: ACCESSIBILITY_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "A typical gathering includes music, a message about Jesus, and time to meet other youths. Please add the approved programme order and duration here: TYPICAL_PROGRAMME_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Services are in SERVICE_LANGUAGE. Please add interpretation or translation support here: INTERPRETATION_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the sound, lighting, quiet-space, and sensory-support details here: SENSORY_SUPPORT_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "You can take things at your own pace. Please add the approved participation and privacy reassurance here: PARTICIPATION_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the approved contact path for meeting someone outside a live service here: COMMUNITY_CONTACT_PATH.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the official social links here: INSTAGRAM_LINK, TELEGRAM_CHANNEL_LINK, WEBSITE_LINK.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the serving process, age requirements, and contact here: SERVING_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the approved follow-up contact here: FOLLOW_UP_CONTACT.",
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
                        "type": "send_message_paraphrased",
                        "text": "At NCC, we believe Jesus is the Son of God who came to reveal God's love and give us new life through His death and resurrection.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add a short approved explanation of Christian belief here: CHRISTIANITY_EXPLANATION.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the approved next-steps explanation and contact here: FOLLOW_JESUS_NEXT_STEPS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the approved Bible, baptism, and discipleship next steps here: FAITH_NEXT_STEPS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the approved prayer response and contact path here: PRAYER_SUPPORT_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the approved private faith-conversation contact path here: FAITH_CONVERSATION_CONTACT.",
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
                        "type": "send_message_paraphrased",
                        "text": "Thank you for sharing that. Please add the approved non-emergency support contact and wording here: WELLBEING_SUPPORT_CONTACT.",
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
                        "type": "send_message_paraphrased",
                        "text": "The nearest toilets are on Level 4 beside the lifts. Ask a Zone team member if you would like someone to show you.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add food, drink, water, refreshment, and cost details here: FOOD_AND_WATER_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the Wi-Fi and charging policy here: WIFI_AND_CHARGING_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the lost-and-found location and contact here: LOST_PROPERTY_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the on-site medical-help and team-member instructions here: ON_SITE_HELP_INSTRUCTIONS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the approved privacy explanation and policy link here: PRIVACY_POLICY_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the approved photo, video, and opt-out policy here: MEDIA_POLICY_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the parental-consent and guardian policy here: PARENTAL_CONSENT_DETAILS.",
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
                        "type": "send_message_paraphrased",
                        "text": "Please add the approved safeguarding, reporting, and behaviour-policy details here: SAFEGUARDING_DETAILS.",
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
                    "llm_gist": "The person asks a genuine question about The Zone, NCC, a youth service, or attending that is not covered by a more specific available question flow.",
                },
                "actions": [
                    {
                        "type": "send_message_paraphrased",
                        "text": "I do not have an approved answer for that yet. Please add the best contact for unanswered questions here: GENERAL_QUESTION_CONTACT.",
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
                        "type": "send_message_paraphrased",
                        "text": "We would love to help you find a group. What is your age or school stage, and what area are you usually in?",
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
                            "llm_gist": "The person answers the current small-group question with their age, school stage, area, availability, or group preference.",
                        },
                        "actions": [
                            {
                                "type": "send_message_paraphrased",
                                "text": "Thanks for sharing. Please add the approved small-group follow-up contact or form here: SMALL_GROUP_CONTACT_OR_LINK.",
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
