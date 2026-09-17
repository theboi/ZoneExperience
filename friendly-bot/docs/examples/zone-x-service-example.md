# Zone X First Development Service

> Canonical functional fixture for the first Friendly Bot service implementation.

| Field | Example value |
| --- | --- |
| Service | Zone X |
| Development use | First seeded service and end-to-end acceptance fixture |
| Service date | Sunday, 18 October 2026 |
| Timezone | Asia/Singapore |
| `highkey` | `true` |
| Doors open | 1:30 pm |
| Doors close | 2:20 pm |
| Service time | 2:30–4:00 pm |
| Service interactions end | 6:00 pm |

## 1. How to read this service

The YAML below is the normative human-readable representation of the first development service. It shows the complete Zone X service, including its service-global checkpoint, latecomer flow, every configured development timestamp, and the complete system-global root. Runtime source data is split into the statically typed Python objects in `seeds/system_global.py` and `seeds/services/zone_x.py`; the two modules together must be equivalent to this configuration. Published flow versions are stored as PostgreSQL `JSONB`.

The configuration uses these rules:

- A `DiscussionFlow` is `trigger → actions → next_flows`.
- `trigger: null` means the harness starts that root automatically.
- A `message` trigger needs exactly one of `llm_gist` or `possible_qns`. Use `possible_qns` for concrete user wording and `llm_gist` for broad or context-dependent conditions.
- A flow's `next_flow_mode` controls reuse of that flow's children.
- `ONE_AND_ONCE_ONLY` removes that choice group after one child succeeds.
- `ALLOW_MANY` keeps that choice group reusable in past selections.
- `CHECKPOINT` is reusable and is also a branch return destination.
- A child with `next_flows: []` returns to the nearest checkpoint after its actions finish.
- Buttons do not contain navigation. A button sends a stable `button_id`; an open `ButtonDiscussionFlowTrigger` with that ID makes the matching flow eligible.
- Present buttons only when the available choices have not already been made clear in the preceding message.
- An action may emit one terminal `ActionEvent`. The matching direct child runs immediately without consulting the LLM.
- An unexpected failure emits `error`. A direct `error` child handles it; otherwise the harness invokes its hardcoded error sender. The hardcoded path is not a `DiscussionFlow` and is not part of published configuration. Action events never bubble.
- All bot prose originates in actions. `send_message_paraphrased` gives the LLM authored copy to rewrite while retaining every fact, instruction, template value, URL, and placeholder. `send_message_llm` gives it a `source` as private reference material: the LLM must answer only the part of the current request that the source supports, using no information from outside it, and must never send the source itself or unrelated source facts. The LLM writes its own wording in lowercase while preserving capitalization from configured source text for names, titles, acronyms, hashtags, places, groups, and ALL_CAPS placeholders. `send_message_fixed` is sent exactly as configured. The LLM may select only eligible flow keys or reserved harness outcomes.

The flow graph, keys, behavior, copy, and outcomes below are the development baseline. The implementation loads equivalent Python seed modules, checks their static types, and validates the resulting data before publication. Any necessary serialization normalization must update this document and the living authorities in the same commit.

## 2. Canonical development configuration

```yaml
system_global_root:
  key: system.global
  trigger:
  actions:
  - type: send_message_paraphrased
    text: Hey {{ user.name }}! Nice to meet you! Welcome to The Zone! I'm Friendly
      Bot, here to help you get connected to our wonderful community!
  - type: send_buttons
    service_bound: false
    buttons:
    - button_id: system.global.menu.timings
      text: when do we gather?
    - button_id: system.global.menu.directions
      text: how to get to service?
    - button_id: system.global.menu.expect
      text: what to expect?
    - button_id: system.global.menu.zone
      text: what is The Zone?
    - button_id: system.global.menu.connect
      text: get connected!
  next_flow_mode: CHECKPOINT
  return_actions:
  - type: send_message_paraphrased
    text: Is there anything else I can help you with? You can ask me anything and
      I will try my best to answer you!
  - type: send_buttons
    service_bound: false
    buttons:
    - button_id: system.global.menu.timings
      text: when do we gather?
    - button_id: system.global.menu.directions
      text: how to get to service?
    - button_id: system.global.menu.expect
      text: what to expect?
    - button_id: system.global.menu.zone
      text: what is The Zone?
    - button_id: system.global.menu.connect
      text: get connected!
  next_flows:
  - key: system.global.never_mind
    trigger:
      type: message
      llm_gist: The person wants to stop, cancel, go back, leave the current topic,
        or says never mind.
    actions:
    - type: return_to_nearest_checkpoint
    next_flow_mode: ONE_AND_ONCE_ONLY
    return_actions: []
    next_flows: []
  - key: system.global.operational.login
    trigger:
      type: command
      command: "/login"
    actions:
    - type: send_message_paraphrased
      text: hey! please reply with the name on your server or leader profile.
    next_flow_mode: ONE_AND_ONCE_ONLY
    return_actions: []
    next_flows:
    - key: system.global.operational.login.name
      trigger:
        type: any_message
      actions:
      - type: capture_operational_login_name
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows:
      - key: system.global.operational.login.name.captured
        trigger:
          type: action_event
          event_key: operational_login.name_captured
        actions:
        - type: send_message_paraphrased
          text: thanks! now reply with your date of birth in DD/MM/YYYY format.
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows:
        - key: system.global.operational.login.dob
          trigger:
            type: any_message
          actions:
          - type: complete_operational_login
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows:
          - key: system.global.operational.login.attached
            trigger:
              type: action_event
              event_key: operational_login.attached
            actions:
            - type: send_message_paraphrased
              text: you are logged in! use /manage whenever you want to update your
                interests, or /logout when you are done.
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
          - key: system.global.operational.login.interests_required
            trigger:
              type: action_event
              event_key: operational_login.interests_required
            actions:
            - type: send_message_paraphrased
              text: you are logged in! tell me one or more interests or conversation
                topics, separated by commas, so i can help make good connections.
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows:
            - key: system.global.operational.login.interests
              trigger:
                type: any_message
              actions:
              - type: save_operational_interests
              next_flow_mode: ONE_AND_ONCE_ONLY
              return_actions: []
              next_flows:
              - key: system.global.operational.login.interests.saved
                trigger:
                  type: action_event
                  event_key: operational_interests.saved
                actions:
                - type: send_message_paraphrased
                  text: saved! your interests are ready to use for matching. use /manage
                    to change them or /logout when you are done.
                next_flow_mode: ONE_AND_ONCE_ONLY
                return_actions: []
                next_flows: []
              - key: system.global.operational.login.interests.invalid
                trigger:
                  type: action_event
                  event_key: operational_interests.invalid
                actions:
                - type: send_message_paraphrased
                  text: please send between one and ten short interests, separated
                    by commas. start /login again when you are ready.
                next_flow_mode: ONE_AND_ONCE_ONLY
                return_actions: []
                next_flows: []
              - key: system.global.operational.login.interests.not_attached
                trigger:
                  type: action_event
                  event_key: operational_interests.not_attached
                actions:
                - type: send_message_paraphrased
                  text: your login is no longer active, so your interests were not
                    changed. please start /login again.
                next_flow_mode: ONE_AND_ONCE_ONLY
                return_actions: []
                next_flows: []
          - key: system.global.operational.login.occupied
            trigger:
              type: action_event
              event_key: operational_login.occupied
            actions:
            - type: send_message_paraphrased
              text: i could not log you in because that profile is currently unavailable.
                please check with a leader if you need help.
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
          - key: system.global.operational.login.not_found
            trigger:
              type: action_event
              event_key: operational_login.not_found
            actions:
            - type: send_message_paraphrased
              text: i could not verify those login details. please check them and
                start /login again.
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
          - key: system.global.operational.login.not_started
            trigger:
              type: action_event
              event_key: operational_login.not_started
            actions:
            - type: send_message_paraphrased
              text: your login request expired. please start /login again.
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
          - key: system.global.operational.login.dob_invalid
            trigger:
              type: action_event
              event_key: operational_login.dob_invalid
            actions:
            - type: send_message_paraphrased
              text: please use a real date in DD/MM/YYYY format and start /login again.
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
      - key: system.global.operational.login.name.invalid
        trigger:
          type: action_event
          event_key: operational_login.name_invalid
        actions:
        - type: send_message_paraphrased
          text: please send the name on your profile and start /login again.
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows: []
  - key: system.global.operational.manage
    trigger:
      type: command
      command: "/manage"
    actions:
    - type: manage_operational_account
    next_flow_mode: ONE_AND_ONCE_ONLY
    return_actions: []
    next_flows:
    - key: system.global.operational.manage.editor
      trigger:
        type: action_event
        event_key: operational_manage.editor
      actions:
      - type: send_message_paraphrased
        text: send your updated interests or conversation topics, separated by commas.
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows:
      - key: system.global.operational.manage.interests
        trigger:
          type: any_message
        actions:
        - type: save_operational_interests
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows:
        - key: system.global.operational.manage.interests.saved
          trigger:
            type: action_event
            event_key: operational_interests.saved
          actions:
          - type: send_message_paraphrased
            text: saved! use /manage whenever you want to change them again.
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []
        - key: system.global.operational.manage.interests.invalid
          trigger:
            type: action_event
            event_key: operational_interests.invalid
          actions:
          - type: send_message_paraphrased
            text: please send between one and ten short interests, separated by commas.
              use /manage to try again.
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []
        - key: system.global.operational.manage.interests.not_attached
          trigger:
            type: action_event
            event_key: operational_interests.not_attached
          actions:
          - type: send_message_paraphrased
            text: your login is no longer active, so your interests were not changed.
              please start /login again.
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []
    - key: system.global.operational.manage.not_attached
      trigger:
        type: action_event
        event_key: operational_manage.not_attached
      actions:
      - type: send_message_paraphrased
        text: you are not logged in as a server or leader. start /login first.
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows: []
  - key: system.global.operational.logout
    trigger:
      type: command
      command: "/logout"
    actions:
    - type: logout_operational_account
    next_flow_mode: ONE_AND_ONCE_ONLY
    return_actions: []
    next_flows:
    - key: system.global.operational.logout.detached
      trigger:
        type: action_event
        event_key: operational_logout.detached
      actions:
      - type: send_message_paraphrased
        text: you are logged out. your profile and interests are still saved for next
          time.
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows: []
    - key: system.global.operational.logout.not_attached
      trigger:
        type: action_event
        event_key: operational_logout.not_attached
      actions:
      - type: send_message_paraphrased
        text: you are not currently logged in as a server or leader.
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows: []
  - key: system.global.thank_you
    trigger:
      type: message
      llm_gist: The person thanks you.
    actions:
    - type: send_message_paraphrased
      text: You're welcome!
    next_flow_mode: ONE_AND_ONCE_ONLY
    return_actions: []
    next_flows: []
  - key: system.global.possible_options
    trigger:
      type: message
      possible_qns:
      - what can you do/help with?
      - what are my options?
      - show me the menu
    actions:
    - type: send_message_paraphrased
      text: You can ask me anything and I will try my best to answer you!
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.safety
    trigger:
      type: message
      llm_gist: Select only when the person's current message clearly says they face
        immediate physical danger, are considering suicide or self-harm, are being
        abused, or urgently need a safe responsible adult. Do not select this for
        greetings, ordinary questions, general distress, ambiguous requests for help,
        jokes, or figurative language.
    actions:
    - type: send_message_paraphrased
      text: Hey, thank you for telling me... you do not have to handle this alone.
        Let me help you find someone to talk to...
    - type: show_activity
      activity: typing
    - type: find_and_reserve_safety_responder
      service_id: "{{ active_service.id | optional }}"
      require_service_attendance_or_always_available: true
      capacity_required: 1
    next_flow_mode: ONE_AND_ONCE_ONLY
    return_actions: []
    next_flows:
    - key: system.global.safety.responder_found
      trigger:
        type: action_event
        event_key: safety_match.found
      actions:
      - type: notify_matched_human
        text: Urgent support request from {{ user.name }}. Please contact them as
          soon as possible. Only information the person provided in this flow may
          be included.
      - type: share_human_contact
        text: "{{ matched_human.name }} is a trusted person you can contact now: {{
          matched_human.telegram_url }}"
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows: []
    - key: system.global.safety.no_responder
      trigger:
        type: action_event
        event_key: safety_match.not_found
      actions:
      - type: send_message_paraphrased
        text: I can't reach a trusted person through the bot right now. If you may
          be in immediate danger, call emergency services or go to a trusted adult
          near you now.
      - type: notify_all_admins
        severity: urgent
        safe_summary: A safety connection request has no eligible responder.
      - type: mark_safety_request_pending
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows: []
  - key: system.global.information.ncc
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks about anything related to New Creation Church (NCC)
        that is not specifically about its youth ministry, The Zone.
    actions:
    - type: send_message_llm
      source: At New Creation Church, we believe we are God's beloved. He demonstrated
        this by freely giving up heaven's best, His only Son Jesus, for you and me.
        When we catch a revelation of this truth, we are transformed by His grace
        from the inside out. That's the beauty of believing and living in our heavenly
        Father's love and grace! No matter who you are or where you come from, there's
        always a place for you in our church family!
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.information.zone
    multi_intent_mode: answer
    trigger:
      type: any_of
      triggers:
      - type: button
        button_id: system.global.menu.timings
      - type: button
        button_id: system.global.menu.zone
      - type: message
        llm_gist: The person asks about anything related to The Zone, or one of its
          youth groups DARE, Arrow or Varsity/V.
    actions:
    - type: send_message_llm
      source: 'The Zone is New Creation Church''s energy-packed youth ministry. It
        reaches out to all secondary and tertiary students as well as NSFs in community
        and in motion for the grace revolution. Centred on the foundation of the Word
        of God, the ministry''s call is the message of God''s unmerited, undeserved
        favour. The Zone is a place for building godly relationships and growing in
        revelation of God''s grace. The Zone is also a place to meet people, explore
        faith, and grow in community. Its additional approved purpose statement is
        THE_ZONE_PURPOSE. The Zone has three youth groups for students and NSFs aged
        13-25: DARE is for secondary school students aged 13-17; Arrow is for post-secondary
        school students and NSFs aged 17-23; Varsity, also called V, is for university
        students. If someone asks for service times without naming a group, tell them
        that there are DARE, Arrow, and Varsity services with distinct schedules and
        ask which group they mean. If a message only names DARE, Arrow, Varsity, or
        V, give that group''s schedule. DARE is a place to discover purpose and meet
        authentic friends who will never let people walk alone. #DAREishome. DARE''s
        Instagram is @nccdare. DARE services are on DARE_SERVICE_DAY. Doors open at
        DARE_DOORS_OPEN_TIME, service starts at DARE_SERVICE_START_TIME, and ends
        at DARE_SERVICE_END_TIME at DARE_SERVICE_VENUE. Arrow is for people in a new
        season; people can come as they are and discover Jesus'' perfect love. #ArrowIsFamily.
        Arrow''s Instagram is @nccarrow. Arrow services are on ARROW_SERVICE_DAY.
        Doors open at ARROW_DOORS_OPEN_TIME, service starts at ARROW_SERVICE_START_TIME,
        and ends at ARROW_SERVICE_END_TIME at ARROW_SERVICE_VENUE. Varsity, or V,
        is a community for university students that values relationships with Jesus
        and each other. Its Instagram is @nccvarsity. Varsity services are on VARSITY_SERVICE_DAY.
        Doors open at VARSITY_DOORS_OPEN_TIME, service starts at VARSITY_SERVICE_START_TIME,
        and ends at VARSITY_SERVICE_END_TIME at VARSITY_SERVICE_VENUE. The next gathering
        is NEXT_GATHERING_DATE at NEXT_GATHERING_TIME. Its event calendar or link
        is UPCOMING_EVENTS_LINK. A typical service lasts SERVICE_DURATION; this may
        differ for the service someone means. For the latest cancellation or service-status
        updates, use OFFICIAL_UPDATES_LINK or contact OFFICIAL_UPDATES_CONTACT. People
        are welcome at The Zone whether or not they are Christian; they can ask questions
        and take things at their own pace. The Zone costs COST_OR_FREE_DETAILS. Event-specific
        price, payment, or financial-help details still need to be added.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.information.zone.timings
    multi_intent_mode: answer
    trigger:
      type: any_of
      triggers:
      - type: button
        button_id: system.global.menu.timings
      - type: message
        llm_gist: The person asks about anything related to The Zone, or one of its
          youth groups DARE, Arrow or Varsity/V.
    actions:
    - type: send_message_fixed
      text: |-
        DARE: for secondary school students aged 13-17yo
        Arrow: for post-secondary school students and NSFs aged 17-23yo
        Varsity: for university students
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.information.directions.star
    multi_intent_mode: answer
    trigger:
      type: any_of
      triggers:
      - type: button
        button_id: system.global.menu.directions
      - type: message
        llm_gist: The person is lost or asks for directions to/within Star Vista/church
    actions:
    - type: send_message_llm
      source: Our services are held at Star Vista! Take the MRT to Buona Vista and
        follow the signs! Once you've reached Star Vista, head to Level 5! (the lift
        only brings you to Level 3, then you need to take the escalators). Wheelchair
        assistance is available upon request.
    - type: send_message_fixed
      text: |-
        1 Vista Exchange Green, Singapore 138617
        https://maps.google.com/?q=The+Star+Performing+Arts+Centre
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.information.handicap_assistance
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks about/for accessibility/handicap assistance.
    actions:
    - type: send_message_paraphrased
      text: If you need help getting to service, let me know again to confirm and
        I will get you in contact with someone who can help!
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.menu.expect
    multi_intent_mode: answer
    trigger:
      type: any_of
      triggers:
      - type: button
        button_id: system.global.menu.expect
      - type: message
        possible_qns:
        - what should i expect?
        - what happens at The Zone?
        - can i come alone?
    actions:
    - type: send_message_paraphrased
      text: Come as you are. You can expect music, a message about Jesus, and time
        to meet other youths. It's okay to come alone, sit quietly, or ask for someone
        to meet you before you enter.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.menu.connect
    multi_intent_mode: answer
    trigger:
      type: any_of
      triggers:
      - type: button
        button_id: system.global.menu.connect
      - type: message
        possible_qns:
        - how do i get connected?
        - can i get the connect link?
    actions:
    - type: send_message_fixed
      text: Get connected at https://bit.ly//thezonenew! We would love to hear from
        you!
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.arrival.first_visit
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - what do i do when i arrive for the first time?
      - where do i go for check-in?
    actions:
    - type: send_message_paraphrased
      text: When you arrive, go to FIRST_TIME_WELCOME_POINT and look for FIRST_TIME_TEAM_DESCRIPTION.
        Please add the check-in details here.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.arrival.registration
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - do i need to register?
      - do i need to buy a ticket?
    actions:
    - type: send_message_paraphrased
      text: 'REGISTRATION_REQUIREMENT. Please add the registration link or instructions
        here: REGISTRATION_LINK.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.arrival.guest
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - can i bring a friend?
      - can my sibling come?
    actions:
    - type: send_message_paraphrased
      text: 'You are welcome to bring a friend. Please add any guest, sibling, parent,
        or guardian requirements here: GUEST_POLICY.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.arrival.late
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - can i still come if i am late?
      - what if service has already started?
    actions:
    - type: send_message_paraphrased
      text: 'You can still come. Please add the late-arrival instructions, entrance,
        and service-specific limits here: LATE_ARRIVAL_INSTRUCTIONS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.travel.bus
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - how do i get to The Zone by bus?
      - which bus goes to Star Vista?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the recommended bus services and stop here: BUS_DIRECTIONS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.travel.drive
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - where can i park?
      - where should my Grab drop me off?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the parking, drop-off, cost, and ride-hailing details here:
        PARKING_AND_DROPOFF_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.travel.entrance
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - which entrance should i use?
      - what level is The Zone on?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the entrance, level, room, and meeting-point details here:
        VENUE_ARRIVAL_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.travel.accessibility
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - is the venue wheelchair accessible?
      - is there a lift?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the venue accessibility arrangements and contact here: ACCESSIBILITY_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.expect.programme
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - what happens during service?
      - what is the programme like?
    actions:
    - type: send_message_paraphrased
      text: 'A typical gathering includes music, a message about Jesus, and time to
        meet other youths. Please add the approved programme order and duration here:
        TYPICAL_PROGRAMME_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.expect.language
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - what language is service in?
      - is there translation?
    actions:
    - type: send_message_paraphrased
      text: 'Services are in SERVICE_LANGUAGE. Please add interpretation or translation
        support here: INTERPRETATION_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.expect.sensory
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - will it be loud?
      - is there a quiet space?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the sound, lighting, quiet-space, and sensory-support details
        here: SENSORY_SUPPORT_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.expect.participation
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - will i have to speak?
      - do i have to join in?
    actions:
    - type: send_message_paraphrased
      text: 'You can take things at your own pace. Please add the approved participation
        and privacy reassurance here: PARTICIPATION_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.meet_someone
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - can i talk to a real person?
      - can i meet someone from The Zone?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the approved contact path for meeting someone outside a live
        service here: COMMUNITY_CONTACT_PATH.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.socials
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - what is The Zone instagram?
      - do you have a Telegram channel?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the official social links here: INSTAGRAM_LINK, TELEGRAM_CHANNEL_LINK,
        WEBSITE_LINK.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.serve
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - how can i volunteer?
      - can i join a team?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the serving process, age requirements, and contact here: SERVING_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.contact_after
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - who can i contact after service?
      - can i talk to someone later this week?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the approved follow-up contact here: FOLLOW_UP_CONTACT.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.jesus
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - who is Jesus?
      - what do Christians believe about Jesus?
    actions:
    - type: send_message_paraphrased
      text: At NCC, we believe Jesus is the Son of God who came to reveal God's love
        and give us new life through His death and resurrection.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.christianity
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - what is Christianity?
      - what do Christians believe?
    actions:
    - type: send_message_paraphrased
      text: 'Please add a short approved explanation of Christian belief here: CHRISTIANITY_EXPLANATION.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.follow_jesus
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - how do i become a Christian?
      - how do i follow Jesus?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the approved next-steps explanation and contact here: FOLLOW_JESUS_NEXT_STEPS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.bible_baptism
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - how do i start reading the Bible?
      - what is baptism?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the approved Bible, baptism, and discipleship next steps here:
        FAITH_NEXT_STEPS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.prayer
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - can you pray for me?
      - how do i pray?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the approved prayer response and contact path here: PRAYER_SUPPORT_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.personal_question
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - can i ask someone a private faith question?
      - can i talk to someone about faith?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the approved private faith-conversation contact path here:
        FAITH_CONVERSATION_CONTACT.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.support.personal
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - i feel really lonely
      - can i talk to someone about something personal?
    actions:
    - type: send_message_paraphrased
      text: 'Thank you for sharing that. Please add the approved non-emergency support
        contact and wording here: WELLBEING_SUPPORT_CONTACT.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.toilet
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - where is the toilet?
      - where is the restroom?
    actions:
    - type: send_message_paraphrased
      text: The nearest toilets are on Level 4 beside the lifts. Ask a Zone team member
        if you would like someone to show you.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.food_water
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - is there food?
      - where can i get water?
    actions:
    - type: send_message_paraphrased
      text: 'Please add food, drink, water, refreshment, and cost details here: FOOD_AND_WATER_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.wifi_charging
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - is there wifi?
      - can i charge my phone?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the Wi-Fi and charging policy here: WIFI_AND_CHARGING_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.lost_property
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - i lost something
      - where is lost and found?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the lost-and-found location and contact here: LOST_PROPERTY_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.medical
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - i do not feel well
      - where can i get first aid?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the on-site medical-help and team-member instructions here:
        ON_SITE_HELP_INSTRUCTIONS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.policy.privacy
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - is my information private?
      - what do you do with my details?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the approved privacy explanation and policy link here: PRIVACY_POLICY_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.policy.photos
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - are photos taken?
      - can i opt out of photos?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the approved photo, video, and opt-out policy here: MEDIA_POLICY_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.policy.parental_consent
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - do i need parental consent?
      - can my parent come with me?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the parental-consent and guardian policy here: PARENTAL_CONSENT_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.policy.safeguarding
    multi_intent_mode: answer
    trigger:
      type: message
      possible_qns:
      - what are the behaviour rules?
      - how do you keep people safe?
    actions:
    - type: send_message_paraphrased
      text: 'Please add the approved safeguarding, reporting, and behaviour-policy
        details here: SAFEGUARDING_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.help.unanswered
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks a genuine question about The Zone, NCC, a youth service,
        or attending that is not covered by a more specific available question flow.
    actions:
    - type: send_message_paraphrased
      text: 'I do not have an approved answer for that yet. Please add the best contact
        for unanswered questions here: GENERAL_QUESTION_CONTACT.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.small_group
    trigger:
      type: message
      possible_qns:
      - how do i join a small group?
      - do you have cell groups?
    actions:
    - type: send_message_paraphrased
      text: We would love to help you find a group. What is your age or school stage,
        and what area are you usually in?
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows:
    - key: system.global.community.small_group.follow_up
      multi_intent_mode: answer
      trigger:
        type: message
        llm_gist: The person answers the current small-group question with their age,
          school stage, area, availability, or group preference.
      actions:
      - type: send_message_paraphrased
        text: 'Thanks for sharing. Please add the approved small-group follow-up contact
          or form here: SMALL_GROUP_CONTACT_OR_LINK.'
      next_flow_mode: ALLOW_MANY
      return_actions: []
      next_flows: []
service:
  key: zone_x_2026_10_18
  name: Zone X
  map_url: https://maps.google.com/?q=The+Star+Performing+Arts+Centre
  timezone: Asia/Singapore
  highkey: true
  doors_open_at: '2026-10-18T13:30:00+08:00'
  doors_close_at: '2026-10-18T14:20:00+08:00'
  service_starts_at: '2026-10-18T14:30:00+08:00'
  service_ends_at: '2026-10-18T16:00:00+08:00'
  interaction_ends_at: '2026-10-18T18:00:00+08:00'
  service_global_root:
    key: service.zone_x.home
    trigger:
    actions:
    - type: send_message_paraphrased
      text: Hey {{ user.name }}! Welcome to Zone X! What would you like help with?
    - type: send_buttons
      service_bound: true
      buttons:
      - button_id: zone_x.menu.directions
        text: Get directions
      - button_id: zone_x.menu.expect
        text: Know what to expect
      - button_id: zone_x.menu.connect
        text: Meet a friendly human
      - button_id: zone_x.menu.change_service
        text: Change service
    next_flow_mode: CHECKPOINT
    return_actions:
    - type: send_message_paraphrased
      text: Is there anything else I can help you with at Zone X?
    - type: send_buttons
      service_bound: true
      buttons:
      - button_id: zone_x.menu.directions
        text: Get directions
      - button_id: zone_x.menu.expect
        text: Know what to expect
      - button_id: zone_x.menu.connect
        text: Meet another friendly human
      - button_id: zone_x.menu.change_service
        text: Change service
    next_flows:
    - key: service.zone_x.directions
      trigger:
        type: any_of
        triggers:
        - type: button
          button_id: zone_x.menu.directions
        - type: message
          possible_qns:
          - how do i get to Zone X?
          - where is Zone X?
      actions:
      - type: send_message_paraphrased
        text: Zone X is held at The Star Performing Arts Centre, 1 Vista Exchange
          Green! Take the MRT to Buona Vista and follow the signs to The Star Vista.
          You'll meet our friendly welcome team in blue near the venue entrance to
          guide you.
      - type: send_message_fixed
        text: 'Map: {{ service.map_url }}'
      next_flow_mode: ALLOW_MANY
      return_actions: []
      next_flows: []
    - key: service.zone_x.what_to_expect
      multi_intent_mode: answer
      trigger:
        type: any_of
        triggers:
        - type: button
          button_id: zone_x.menu.expect
        - type: message
          possible_qns:
          - what should i expect at Zone X?
          - can i come alone?
      actions:
      - type: send_message_paraphrased
        text: Come as you are. You can expect music, a message about Jesus, and time
          to meet other youths. It’s okay to come alone, sit quietly, or ask for someone
          to meet you before you enter.
      next_flow_mode: ALLOW_MANY
      return_actions: []
      next_flows: []
    - key: service.zone_x.connect.start
      trigger:
        type: any_of
        triggers:
        - type: button
          button_id: zone_x.menu.connect
        - type: message
          possible_qns:
          - can i meet someone?
          - can i talk to a friendly human?
      actions:
      - type: send_message_paraphrased
        text: What is one thing that interests you? Nothing is a valid answer too!
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows:
      - key: service.zone_x.connect.capture_interest
        trigger:
          type: message
          llm_gist: The person answers the question about an interest or says they
            do not have one.
        actions:
        - type: save_incoming
          field: human_match_request.interest
          preserve_exact_text: true
        - type: show_activity
          activity: typing
        - type: find_and_reserve_server
          service_id: "{{ service.id }}"
          require_service_attendance: true
          capacity_required: 1
          rank_with:
          - human_match_request.interest
          - candidate.interests
          - candidate.cg_name
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows:
        - key: service.zone_x.connect.match_found
          trigger:
            type: action_event
            event_key: human_match.found
          actions:
          - type: send_message_paraphrased
            text: I found {{ matched_server.name }} from {{ matched_server.cg_name
              }}. How would you like to meet?
          - type: send_buttons
            service_bound: true
            buttons:
            - button_id: zone_x.connect.join_group
              text: Join {{ matched_server.name }}
            - button_id: zone_x.connect.join_me
              text: Ask {{ matched_server.name }} to join me
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows:
          - key: service.zone_x.connect.join_group
            trigger:
              type: button
              button_id: zone_x.connect.join_group
            actions:
            - type: confirm_human_match
              meeting_preference: nbnc_joins_human
            - type: notify_matched_human
              text: "{{ user.name }} is at {{ service.name }} and would like to join
                you. Their interest: {{ human_match_request.interest }}. They may
                contact you on Telegram."
            - type: share_human_contact
              text: "{{ matched_server.name }} is expecting you. Message them here:
                {{ matched_server.telegram_url }}"
            - type: send_message_paraphrased
              text: if {{ matched_server.name }} is not responding, tell me and i
                will find someone else.
            next_flow_mode: ALLOW_MANY
            return_actions: []
            next_flows:
            - key: service.zone_x.connect.join_group.not_responding
              trigger:
                type: message
                llm_gist: The matched person is not responding or cannot be reached.
              actions:
              - type: release_human_match
              - type: notify_previous_human
                text: "{{ user.name }} reported that they could not reach you. Their
                  connection request will be reassigned."
              - type: exclude_previous_human_from_next_attempt
              - type: show_activity
                activity: typing
              - type: find_and_reserve_server
                service_id: "{{ service.id }}"
                require_service_attendance: true
                capacity_required: 1
                preserve_meeting_preference: true
              next_flow_mode: ONE_AND_ONCE_ONLY
              return_actions: []
              next_flows:
              - key: service.zone_x.connect.join_group.rematch_found
                trigger:
                  type: action_event
                  event_key: human_match.found
                actions:
                - type: notify_matched_human
                  text: "{{ user.name }} is at {{ service.name }} and would like to
                    join you. Their interest: {{ human_match_request.interest }}.
                    They may contact you on Telegram."
                - type: share_human_contact
                  text: 'Try {{ matched_server.name }} instead: {{ matched_server.telegram_url
                    }}'
                next_flow_mode: ONE_AND_ONCE_ONLY
                return_actions: []
                next_flows: []
              - key: service.zone_x.connect.join_group.rematch_not_found
                trigger:
                  type: action_event
                  event_key: human_match.not_found
                actions:
                - type: send_message_paraphrased
                  text: Sorry, nobody else is available to meet right now. Please
                    speak to a Zone team member at the venue.
                next_flow_mode: ONE_AND_ONCE_ONLY
                return_actions: []
                next_flows: []
          - key: service.zone_x.connect.join_me
            trigger:
              type: button
              button_id: zone_x.connect.join_me
            actions:
            - type: confirm_human_match
              meeting_preference: human_joins_nbnc
            - type: notify_matched_human
              text: "{{ user.name }} is at {{ service.name }} and would like you to
                join them. Their interest: {{ human_match_request.interest }}. They
                may contact you on Telegram."
            - type: share_human_contact
              text: "{{ matched_server.name }} will come and meet you. Message them
                here so you can find each other: {{ matched_server.telegram_url }}"
            - type: send_message_paraphrased
              text: if {{ matched_server.name }} is not responding, tell me and i
                will find someone else.
            next_flow_mode: ALLOW_MANY
            return_actions: []
            next_flows:
            - key: service.zone_x.connect.join_me.not_responding
              trigger:
                type: message
                llm_gist: The matched person is not responding or cannot be reached.
              actions:
              - type: release_human_match
              - type: notify_previous_human
                text: "{{ user.name }} reported that they could not reach you. Their
                  connection request will be reassigned."
              - type: exclude_previous_human_from_next_attempt
              - type: show_activity
                activity: typing
              - type: find_and_reserve_server
                service_id: "{{ service.id }}"
                require_service_attendance: true
                capacity_required: 1
                preserve_meeting_preference: true
              next_flow_mode: ONE_AND_ONCE_ONLY
              return_actions: []
              next_flows:
              - key: service.zone_x.connect.join_me.rematch_found
                trigger:
                  type: action_event
                  event_key: human_match.found
                actions:
                - type: notify_matched_human
                  text: "{{ user.name }} is at {{ service.name }} and would like you
                    to join them. Their interest: {{ human_match_request.interest
                    }}. They may contact you on Telegram."
                - type: share_human_contact
                  text: 'Try {{ matched_server.name }} instead: {{ matched_server.telegram_url
                    }}'
                next_flow_mode: ONE_AND_ONCE_ONLY
                return_actions: []
                next_flows: []
              - key: service.zone_x.connect.join_me.rematch_not_found
                trigger:
                  type: action_event
                  event_key: human_match.not_found
                actions:
                - type: send_message_paraphrased
                  text: Sorry, nobody else is available to meet right now. Please
                    speak to a Zone team member at the venue.
                next_flow_mode: ONE_AND_ONCE_ONLY
                return_actions: []
                next_flows: []
        - key: service.zone_x.connect.no_match
          trigger:
            type: action_event
            event_key: human_match.not_found
          actions:
          - type: send_message_paraphrased
            text: Sorry, nobody is available to meet right now. Please try again later
              or speak to a Zone team member at the venue.
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []
    - key: service.zone_x.change_service
      trigger:
        type: any_of
        triggers:
        - type: button
          button_id: zone_x.menu.change_service
        - type: message
          possible_qns:
          - can i switch service?
          - i am going to a different service
      actions:
      - type: resolve_service_switch_options
        preserve_historical_attendance: true
        replace_active_overlapping_service: true
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows:
      - key: service.zone_x.change_service.choice_required
        trigger:
          type: action_event
          event_key: service_attendance.choice_required
        actions:
        - type: send_service_choice_buttons
          button_id: service.attendance.select
          text: Which service would you like to attend?
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows:
        - key: service.zone_x.change_service.select
          trigger:
            type: button
            button_id: service.attendance.select
          actions:
          - type: select_service_attendance
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows:
          - key: service.zone_x.change_service.selected
            trigger:
              type: action_event
              event_key: service_attendance.selected
            actions:
            - type: enter_selected_service_checkpoint
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
          - key: service.zone_x.change_service.latecomer
            trigger:
              type: action_event
              event_key: service_attendance.latecomer
            actions:
            - type: enter_selected_service_latecomer_flow
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
          - key: service.zone_x.change_service.ended
            trigger:
              type: action_event
              event_key: service_attendance.ended
            actions:
            - type: send_message_paraphrased
              text: Sorry, the service is over!
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
      - key: service.zone_x.change_service.none_available
        trigger:
          type: action_event
          event_key: service_attendance.none_available
        actions:
        - type: send_message_paraphrased
          text: There are no other ongoing services to switch to.
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows: []
  latecomer_flow:
    key: service.zone_x.latecomer
    trigger:
    actions:
    - type: add_service_attendance
      attendance_status: latecomer
    - type: enter_service_checkpoint
      flow_key: service.zone_x.home
    - type: send_message_paraphrased
      text: Yes, you can still join Zone X. Service has started, so head to the venue
        entrance and ask a Zone team member to help you find a seat.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  timestamps:
  - key: zone_x.marketing_starts
    occurs_at: '2026-09-27T10:00:00+08:00'
    audience: ALL_NBNCS
    root_flow:
      key: service.zone_x.timestamp.marketing
      trigger:
      actions:
      - type: send_photo
        asset_key: zone_x_poster_2026
        caption: Zone X is happening on 18 October at The Star Performing Arts Centre.
          You can come alone. We’ll help you meet someone friendly.
      - type: send_buttons
        service_bound: true
        buttons:
        - button_id: zone_x.marketing.what_to_expect
          text: Know what to expect
        - button_id: zone_x.marketing.directions
          text: Get directions
      next_flow_mode: CHECKPOINT
      return_actions:
      - type: send_message_paraphrased
        text: Would you like to know anything else about Zone X?
      next_flows:
      - key: service.zone_x.timestamp.marketing.expect
        trigger:
          type: button
          button_id: zone_x.marketing.what_to_expect
        actions:
        - type: send_message_paraphrased
          text: Come as you are. There will be music, a message about Jesus, and friendly
            people who can sit with you.
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows: []
      - key: service.zone_x.timestamp.marketing.directions
        trigger:
          type: button
          button_id: zone_x.marketing.directions
        actions:
        - type: send_message_fixed
          text: 'Take the MRT to Buona Vista and follow signs to The Star Vista. Map:
            {{ service.map_url }}'
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows: []
  - key: zone_x.one_day_before
    occurs_at: '2026-10-17T14:30:00+08:00'
    audience: ALL_NBNCS
    root_flow:
      key: service.zone_x.timestamp.one_day_before
      trigger:
      actions:
      - type: send_message_paraphrased
        text: Zone X is tomorrow at 2:30 pm. Doors open at 1:30 pm at The Star Performing
          Arts Centre.
      - type: send_message_fixed
        text: 'Map: {{ service.map_url }}'
      next_flow_mode: CHECKPOINT
      return_actions:
      - type: send_message_paraphrased
        text: Anything else you’d like to know before Zone X?
      next_flows: []
  - key: zone_x.doors_open
    occurs_at: '2026-10-18T13:30:00+08:00'
    audience: ALL_NBNCS
    root_flow:
      key: service.zone_x.timestamp.doors_open
      trigger:
      actions:
      - type: send_message_paraphrased
        text: Doors are open for Zone X. Are you here with us? Reply 'i am here' to
          check in.
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows:
      - key: service.zone_x.timestamp.doors_open.check_in
        trigger:
          type: message
          possible_qns:
          - i am here
          - i'm here
          - yes, i am at Zone X
        actions:
        - type: select_service_attendance
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows:
        - key: service.zone_x.timestamp.doors_open.attending
          trigger:
            type: action_event
            event_key: service_attendance.selected
          actions:
          - type: enter_selected_service_checkpoint
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []
        - key: service.zone_x.timestamp.doors_open.latecomer
          trigger:
            type: action_event
            event_key: service_attendance.latecomer
          actions:
          - type: enter_selected_service_latecomer_flow
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []
        - key: service.zone_x.timestamp.doors_open.ended
          trigger:
            type: action_event
            event_key: service_attendance.ended
          actions:
          - type: send_message_paraphrased
            text: Sorry, the service is over!
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []
  - key: zone_x.service_starts
    occurs_at: '2026-10-18T14:30:00+08:00'
    audience: SERVICE_NBNCS
    root_flow:
      key: service.zone_x.timestamp.service_questions
      trigger:
      actions:
      - type: send_message_paraphrased
        text: Service has started. You can ask a question here at any time.
      - type: send_buttons
        service_bound: true
        buttons:
        - button_id: zone_x.service.toilet
          text: Where is the toilet?
        - button_id: zone_x.service.who_is_jesus
          text: Who is Jesus?
      next_flow_mode: CHECKPOINT
      return_actions:
      - type: send_message_paraphrased
        text: Is there anything else you’d like to ask about service?
      - type: send_buttons
        service_bound: true
        buttons:
        - button_id: zone_x.service.toilet
          text: Where is the toilet?
        - button_id: zone_x.service.who_is_jesus
          text: Who is Jesus?
      next_flows:
      - key: service.zone_x.service.toilet
        multi_intent_mode: answer
        trigger:
          type: any_of
          triggers:
          - type: button
            button_id: zone_x.service.toilet
          - type: message
            possible_qns:
            - where is the toilet?
            - where is the restroom?
        actions:
        - type: send_message_paraphrased
          text: The nearest toilets are on Level 4 beside the lifts. Ask a Zone team
            member if you’d like someone to show you.
        next_flow_mode: ALLOW_MANY
        return_actions: []
        next_flows: []
      - key: service.zone_x.service.who_is_jesus
        multi_intent_mode: answer
        trigger:
          type: any_of
          triggers:
          - type: button
            button_id: zone_x.service.who_is_jesus
          - type: message
            possible_qns:
            - who is Jesus?
            - what do Christians believe about Jesus?
        actions:
        - type: send_message_paraphrased
          text: At NCC, we believe Jesus is the Son of God who came to reveal God’s
            love and give us new life through His death and resurrection. I can connect
            you with someone if you’d like to talk about this personally.
        next_flow_mode: ALLOW_MANY
        return_actions: []
        next_flows: []
      - key: service.zone_x.service.unknown_question
        multi_intent_mode: answer
        trigger:
          type: message
          llm_gist: The person asks a genuine question about service that is not covered
            by a more specific open question flow.
        actions:
        - type: send_message_paraphrased
          text: I don’t have an approved answer for that question, but I can connect
            you with someone who can talk with you.
        next_flow_mode: ALLOW_MANY
        return_actions: []
        next_flows: []
  - key: zone_x.service_ends
    occurs_at: '2026-10-18T16:00:00+08:00'
    audience: ALL_SERVICE_ATTENDEES
    root_flow:
      key: service.zone_x.timestamp.after_service
      trigger:
      actions:
      - type: send_message_paraphrased
        text: Service has ended. What would you like to do next?
      - type: send_buttons
        service_bound: true
        buttons:
        - button_id: zone_x.after.connect
          text: Connect with us
        - button_id: zone_x.after.ask
          text: Ask a question
      next_flow_mode: CHECKPOINT
      return_actions:
      - type: send_message_paraphrased
        text: Would you like help with anything else before you go?
      next_flows:
      - key: service.zone_x.after.connect
        trigger:
          type: button
          button_id: zone_x.after.connect
        actions:
        - type: send_message_paraphrased
          text: I can introduce you to someone friendly. Tell me if you would like
            me to do that.
        next_flow_mode: ALLOW_MANY
        return_actions: []
        next_flows: []
      - key: service.zone_x.after.ask
        trigger:
          type: button
          button_id: zone_x.after.ask
        actions:
        - type: send_message_paraphrased
          text: A friendly human can help with your question. Tell me if you would
            like an introduction.
        next_flow_mode: ALLOW_MANY
        return_actions: []
        next_flows: []
  - key: zone_x.thank_you
    occurs_at: '2026-10-18T17:30:00+08:00'
    audience: ALL_SERVICE_ATTENDEES
    root_flow:
      key: service.zone_x.timestamp.thank_you
      trigger:
      actions:
      - type: send_message_paraphrased
        text: Thank you for coming to Zone X today. We’re glad you were here. You
          can still use the service options until 6:00 pm.
      next_flow_mode: ALLOW_MANY
      return_actions: []
      next_flows: []
  - key: zone_x.interaction_ends
    occurs_at: '2026-10-18T18:00:00+08:00'
    audience: ALL_SERVICE_ATTENDEES
    root_flow:
      key: service.zone_x.timestamp.interaction_ends
      trigger:
      actions:
      - type: send_message_paraphrased
        text: Zone X has ended, but you can still ask for directions to Star or learn
          more about NCC here anytime.
      - type: end_service_interactions
        expire_service_bound_selections: true
        release_service_match_capacity: true
        return_to_system_checkpoint: true
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows: []
```

## 3. What the user actually experiences

### 3.1 A new NBNC arrives during the highkey check-in window

There is one ongoing highkey service, so the bot does not ask whether they are attending.

```text
Alex: hi

Bot: Hey! Welcome to The Zone! Glad to see you here today!
Bot: How may I address you?

Alex: Alex Tan

Bot: You’re checked in for Zone X. What would you like to do?
     [Get directions]
     [Know what to expect]
     [Meet a friendly human]
     [Change service]
```

After onboarding, the important open selections are:

| Parent flow | State | Why it is eligible |
| --- | --- | --- |
| `system.global` | Global, reusable | Safety and “never mind” must always be considered |
| `service.zone_x.home` | Current checkpoint | This was the most recently presented choice group |

### 3.2 Alex asks for directions

```text
Alex taps: Get directions

Bot: Zone X is at The Star Performing Arts Centre, 1 Vista Exchange Green…
     [Open map]

Bot: Is there anything else you’d like help with at Zone X?
     [Get directions] [Know what to expect]
     [Meet another friendly human] [Change service]
```

`service.zone_x.directions` is a leaf after its optional map child finishes, so the branch returns to `service.zone_x.home`. Because the home flow is a checkpoint, the bot runs `return_actions` instead of its first-entry `actions`.

### 3.3 A timestamp arrives while another question is waiting

Suppose Alex has tapped “Meet a friendly human” and the bot is waiting for an interest. At 2:30 pm, the service timestamp also arrives.

```text
Bot: What is one thing that interests you? Nothing is a valid answer too!

Bot: Service has started. You can ask a question here at any time.
     [Where is the toilet?] [Who is Jesus?]
```

Both branches remain current:

| Parent flow | State | Open children |
| --- | --- | --- |
| `service.zone_x.connect.start` | Current | `capture_interest` |
| `service.zone_x.timestamp.service_questions` | Current checkpoint | `toilet`, `who_is_jesus`, `unknown_question` |
| `service.zone_x.home` | Reusable checkpoint | Directions, expectations, human connection, change service |
| `system.global` | Global, reusable checkpoint | Safety, never mind |

If Alex writes `football`, the router should prefer `service.zone_x.connect.capture_interest`. It must leave the service-question checkpoint open.

### 3.4 Native reply gives context without hard-scoping

Alex can swipe-reply to the service message:

```text
Alex replies to “Service has started…”: Who is Jesus?

Bot: At NCC, we believe Jesus is the Son of God…
     [Talk to someone]
```

The router receives the replied-to text as a strong hint. It still receives every open candidate, so an urgent safety message or a clear response to another current flow can win instead.

### 3.5 Friendly-human matching

Assume Alex said `football`. The matching action considers only attending users whose operational role is exactly `server` and who have available capacity. Leaders and staff are excluded. It ranks server interests and `cg_name`, then temporarily reserves the best candidate before presenting the meeting choice.

```text
Bot: I found Jordan from North CG. How would you like to meet?
     [Join Jordan]
     [Ask Jordan to join me]

Alex taps: Join Jordan

Bot to Jordan: Alex Tan is at Zone X and would like to join you.
               Their interest: football. They may contact you on Telegram.

Bot to Alex: Jordan is expecting you. Message them here:
             https://t.me/jordan_example
             [Jordan is not responding]
```

There is no accept or decline step. Jordan's capacity is occupied until the service interaction boundary, a rematch, or admin intervention.

If Alex taps the old “Jordan is not responding” button, its `button_id` is evaluated against reusable past selections. The bot releases Jordan, notifies Jordan, excludes Jordan from this next attempt, and chooses another eligible person. The same service-bound button stops working after 6:00 pm and produces “Sorry, the service is over!”

### 3.6 A vague cancellation

If Alex simply writes `/cancel`, nothing happens unless a configured command trigger exists. This example does not define `/cancel`.

If Alex says `never mind`, the system-global semantic trigger can run. The `return_to_nearest_checkpoint` action operates on the focused branch. When two branches are equally plausible, the router must return `system.clarify_ambiguous_context`, and the harness sends:

```text
Bot: Sorry, which message were you referring to?
```

Alex can then use Telegram's native reply on the message they meant.

## 4. How selection state changes

This condensed trace shows the difference between the three modes.

| Moment | Selection change |
| --- | --- |
| Service entry | `service.zone_x.home` becomes current and a checkpoint |
| Select `connect.start` | Home remains reusable; `connect.start` becomes current |
| Answer the interest question | `connect.start` is removed completely because it is `ONE_AND_ONCE_ONLY`; `capture_interest` becomes current |
| Pick “Join Jordan” | `capture_interest` is removed completely; `join_group` becomes current |
| Service timestamp fires | `service_questions` is appended to current; `join_group` remains current |
| Ask “Who is Jesus?” | Service choice runs, then returns to the service checkpoint; `join_group` stays current |
| Report “not responding” | `join_group` remains reusable because it is `ALLOW_MANY`; its child rematches Alex |
| A branch reaches a leaf | Only that branch returns to its nearest checkpoint; unrelated current branches remain unchanged |
| Interaction ends | All Zone X-bound selections expire, match capacity is released, and the system checkpoint becomes the default context |

## 5. Cases this example is meant to expose

Please pay particular attention to these concrete assumptions:

1. **Marketing recipients:** marketing and pre-service reminders target `ALL_NBNCS`, even though those people have not confirmed attendance. Their timestamp roots are checkpoints so their replies have a valid branch-local return target without enrolling them.
2. **Highkey check-in:** a new NBNC messaging between doors open and doors close is automatically enrolled when Zone X is the only ongoing highkey service. The “Check in to Zone X” button is mainly for people who received the doors-open broadcast before writing to the bot. Its attendance action must check the current time: after doors close, the same old button runs the latecomer path instead of silently recording ordinary attendance.
3. **Capacity reservation:** the candidate is reserved immediately after the interest reply, before the NBNC chooses who joins whom. This prevents two NBNCs from being shown the same last available person.
4. **Matching role pools:** normal matching uses only the exact `server` role and requires current Zone X attendance. Safety uses only staff or leaders and allows `always_available` instead of attendance. Audience role inheritance does not alter matching eligibility.
5. **Action outcomes:** complex actions emit one terminal `ActionEvent`, and a matching direct child continues the flow. `human_match.not_found` is an ordinary outcome rather than an exception or fallback.
6. **Never-mind traversal:** the explicit `return_to_nearest_checkpoint` action counts as the branch return; the generic empty-leaf rule must not perform a second return afterward.
7. **Repeated rematch button ID:** both meeting-preference branches use `zone_x.connect.not_responding`. Only the selected branch is open, so the same button ID is expected to be unambiguous.
8. **Development content:** the directions, toilet location, theological answer, urgent-support wording, and all names/URLs are canonical seed values for development and acceptance tests, but still require operational approval before a real launch.
9. **Service switching:** `resolve_service_switch_options` is a typed complex action because the buttons depend on whichever other services are active at runtime. It emits an outcome handled by a direct child flow.
10. **Interaction expiry:** at 6:00 pm, Zone X flows and buttons expire even if an earlier prompt is unanswered. The long-term conversation remains stored.
11. **Default errors:** an executing flow may define a direct `error` event child. If it does not, the harness sends the hardcoded message “Sorry, an error occurred. Error log: {telegram_user_id}.” without searching any ancestor. This sender is application code, not a root flow or configurable JSON. The Telegram user ID is rendered locally and never sent to OpenRouter.

If any assumption above is wrong, changing it may affect the product specification or architecture masterplan, not just this example.
