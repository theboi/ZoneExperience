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

The YAML below is the normative human-readable representation of the first development service. It shows the complete Zone X service, including its service-global checkpoint, latecomer flow, every configured development timestamp, and the complete system-global root. Runtime source data is split into `seeds/system-global.json` and `seeds/services/zone-x.json`; the two documents together must be equivalent to this configuration. Published flow versions are stored as PostgreSQL `JSONB`.

The configuration uses these rules:

- A `DiscussionFlow` is `trigger → actions → next_flows`.
- `trigger: null` means the harness starts that root automatically.
- A flow's `next_flow_mode` controls reuse of that flow's children.
- `ONE_AND_ONCE_ONLY` removes that choice group after one child succeeds.
- `ALLOW_MANY` keeps that choice group reusable in past selections.
- `CHECKPOINT` is reusable and is also a branch return destination.
- A child with `next_flows: []` returns to the nearest checkpoint after its actions finish.
- Buttons do not contain navigation. A button sends a stable `button_id`; an open `ButtonDiscussionFlowTrigger` with that ID makes the matching flow eligible.
- An action may emit one terminal `ActionEvent`. The matching direct child runs immediately without consulting the LLM.
- An unexpected failure emits `error`. A direct `error` child handles it; otherwise the harness invokes its hardcoded error sender. The hardcoded path is not a `DiscussionFlow` and is not part of published configuration. Action events never bubble.
- All bot prose comes from actions. The LLM may return only one of the open flow keys or a reserved harness key.

The flow graph, keys, behavior, copy, and outcomes below are the development baseline. The implementation stores an equivalent JSON seed. Any necessary serialization normalization must update this document and the living authorities in the same commit.

## 2. Canonical development configuration

```yaml
system_global_root:
  key: system.global
  trigger: null
  actions:
  - type: send_message
    text: Hey {{ user.name }}! Nice to meet you! Welcome to The Zone! I'm Friendly
      Bot, here to help you get connected to our wonderful community!
  - type: send_buttons
    service_bound: false
    buttons:
    - button_id: system.global.menu.timings
      text: When do we gather?
    - button_id: system.global.menu.directions
      text: How to get to service?
    - button_id: system.global.menu.expect
      text: What to expect?
    - button_id: system.global.menu.zone
      text: What is The Zone?
    - button_id: system.global.menu.connect
      text: Get connected
  next_flow_mode: CHECKPOINT
  return_actions:
  - type: send_message
    text: Is there anything else I can help you with?
  - type: send_buttons
    service_bound: false
    buttons:
    - button_id: system.global.menu.timings
      text: When do we gather?
    - button_id: system.global.menu.directions
      text: How to get to service?
    - button_id: system.global.menu.expect
      text: What to expect?
    - button_id: system.global.menu.zone
      text: What is The Zone?
    - button_id: system.global.menu.connect
      text: Get connected
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
  - key: system.global.safety
    trigger:
      type: message
      llm_gist: Select only when the person's current message clearly says they face
        immediate physical danger, are considering suicide or self-harm, are being
        abused, or urgently need a safe responsible adult. Do not select this for
        greetings, ordinary questions, general distress, ambiguous requests for help,
        jokes, or figurative language.
    actions:
    - type: send_message
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
      - type: send_message
        text: I can’t reach a trusted person through the bot right now. If you may
          be in immediate danger, call emergency services or go to a trusted adult
          near you now.
      - type: notify_all_admins
        severity: urgent
        safe_summary: A safety connection request has no eligible responder.
      - type: mark_safety_request_pending
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows: []
  - key: system.global.options
    trigger:
      type: message
      llm_gist: The person asks what options are available, what the bot can do, how
        the bot can help, or asks for a menu.
    actions:
    - type: return_to_nearest_checkpoint
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.menu.timings
    multi_intent_mode: interactive
    trigger:
      type: any_of
      triggers:
      - type: button
        button_id: system.global.menu.timings
      - type: message
        llm_gist: The person asks when youth services or gatherings happen but does
          not name a specific service or group.
    actions:
    - type: send_message
      text: which service are you asking about? you can choose Arrow below or reply
        with a service name.
    - type: send_buttons
      service_bound: false
      buttons:
      - button_id: system.global.menu.timings.arrow
        text: Arrow
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows:
    - key: system.global.menu.timings.arrow
      multi_intent_mode: answer
      trigger:
        type: any_of
        triggers:
        - type: button
          button_id: system.global.menu.timings.arrow
        - type: message
          llm_gist: The person answers the current service-timing question by naming
            Arrow or asks for Arrow service timings.
      actions:
      - type: send_message
        text: Arrow services are held on ARROW_SERVICE_DAY. Doors open at ARROW_DOORS_OPEN_TIME,
          service starts at ARROW_SERVICE_START_TIME, and ends at ARROW_SERVICE_END_TIME
          at ARROW_SERVICE_VENUE.
      next_flow_mode: ALLOW_MANY
      return_actions: []
      next_flows: []
    - key: system.global.menu.timings.other_service
      multi_intent_mode: answer
      trigger:
        type: message
        llm_gist: The person answers the current service-timing question with a service
          or group other than Arrow.
      actions:
      - type: send_message
        text: I only have Arrow timing details set up right now. Please add OTHER_SERVICE_NAME,
          OTHER_SERVICE_DAY, OTHER_SERVICE_START_TIME, and OTHER_SERVICE_VENUE here.
      next_flow_mode: ALLOW_MANY
      return_actions: []
      next_flows: []
  - key: system.global.menu.directions
    multi_intent_mode: answer
    trigger:
      type: any_of
      triggers:
      - type: button
        button_id: system.global.menu.directions
      - type: message
        llm_gist: The person asks for directions to the Star Performing Arts Centre,
          Star Vista, The Zone or church.
    actions:
    - type: send_message
      text: Our services are held at Star Vista! Take the MRT to Buona Vista and follow
        the signs!
    - type: send_message_fixed
      text: |-
        1 Vista Exchange Green, Singapore 138617
        https://maps.google.com/?q=The+Star+Performing+Arts+Centre
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
        llm_gist: The person asks what to expect at The Zone, what will happen, what
          to wear, or whether they may come alone.
    actions:
    - type: send_message
      text: Come as you are. You can expect music, a message about Jesus, and time
        to meet other youths. It’s okay to come alone, sit quietly, or ask for someone
        to meet you before you enter.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.menu.zone
    multi_intent_mode: answer
    trigger:
      type: any_of
      triggers:
      - type: button
        button_id: system.global.menu.zone
      - type: message
        llm_gist: The person asks what The Zone is, what this youth community is,
          or wants to learn about The Zone.
    actions:
    - type: send_message
      text: The Zone is a youth community where you can come as you are, meet other
        youths, and learn about Jesus.
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
        llm_gist: The person wants to get connected with The Zone or asks for a connection
          link.
    actions:
    - type: send_message_fixed
      text: Get connected at https://bit.ly//thezonenew! We would love to hear from
        you!
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.schedule.arrow
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person explicitly asks when Arrow services or gatherings happen.
    actions:
    - type: send_message
      text: Arrow services are held on ARROW_SERVICE_DAY. Doors open at ARROW_DOORS_OPEN_TIME,
        service starts at ARROW_SERVICE_START_TIME, and ends at ARROW_SERVICE_END_TIME
        at ARROW_SERVICE_VENUE.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.schedule.next_gathering
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether there is a youth service today, tomorrow,
        this week, or asks for the next gathering date.
    actions:
    - type: send_message
      text: 'The next gathering is NEXT_GATHERING_DATE at NEXT_GATHERING_TIME. Please
        add the current event calendar or link: UPCOMING_EVENTS_LINK.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.schedule.duration
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks how long a youth service, gathering, or event lasts.
    actions:
    - type: send_message
      text: A typical service lasts SERVICE_DURATION. Please update this if it differs
        for the service you mean.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.schedule.status
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether a service or event is cancelled, moved, postponed,
        or still happening.
    actions:
    - type: send_message
      text: Please check OFFICIAL_UPDATES_LINK for the latest service updates, or
        contact OFFICIAL_UPDATES_CONTACT.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.about.ncc
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks what NCC is, what NCC stands for, or whether The Zone
        is part of a church.
    actions:
    - type: send_message
      text: 'NCC is New Creation Church. Please add a short approved description here:
        NCC_DESCRIPTION.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.about.eligibility
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks who The Zone is for, the age range, whether it is
        for students, or whether they are allowed to come.
    actions:
    - type: send_message
      text: 'The Zone is for YOUTH_AGE_RANGE. Please add the school stages or ages
        that are welcome: ELIGIBILITY_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.about.not_christian
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether they may come if they are not Christian, unsure
        about faith, or only want to check The Zone out.
    actions:
    - type: send_message
      text: You are welcome at The Zone whether or not you are Christian. You can
        come, ask questions, and take things at your own pace.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.about.purpose
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks why they should come, what the point of The Zone is,
        or what makes it different.
    actions:
    - type: send_message
      text: 'The Zone is a place to meet people, explore faith, and grow in community.
        Please add the approved purpose statement here: THE_ZONE_PURPOSE.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.about.cost
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether The Zone, a service, or an event costs money.
    actions:
    - type: send_message
      text: The Zone costs COST_OR_FREE_DETAILS. Please add any event-specific price,
        payment, or financial-help details here.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.arrival.first_visit
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks what to do on their first visit, where to go when
        they arrive, or how check-in works.
    actions:
    - type: send_message
      text: When you arrive, go to FIRST_TIME_WELCOME_POINT and look for FIRST_TIME_TEAM_DESCRIPTION.
        Please add the check-in details here.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.arrival.registration
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether they need to register, buy a ticket, book
        a place, or check in before coming.
    actions:
    - type: send_message
      text: 'REGISTRATION_REQUIREMENT. Please add the registration link or instructions
        here: REGISTRATION_LINK.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.arrival.guest
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether they may bring a friend, sibling, parent,
        or guardian.
    actions:
    - type: send_message
      text: 'You are welcome to bring a friend. Please add any guest, sibling, parent,
        or guardian requirements here: GUEST_POLICY.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.arrival.late
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether they can still come if they are late, the
        service has started, or they missed the beginning.
    actions:
    - type: send_message
      text: 'You can still come. Please add the late-arrival instructions, entrance,
        and service-specific limits here: LATE_ARRIVAL_INSTRUCTIONS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.travel.bus
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks how to get to The Zone or Star Vista by bus.
    actions:
    - type: send_message
      text: 'Please add the recommended bus services and stop here: BUS_DIRECTIONS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.travel.drive
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks about driving, parking, a drop-off point, or ride-hailing
        to The Zone or Star Vista.
    actions:
    - type: send_message
      text: 'Please add the parking, drop-off, cost, and ride-hailing details here:
        PARKING_AND_DROPOFF_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.travel.entrance
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks which entrance, level, room, or meeting point to use
        at the venue.
    actions:
    - type: send_message
      text: 'Please add the entrance, level, room, and meeting-point details here:
        VENUE_ARRIVAL_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.travel.accessibility
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks about wheelchair access, lifts, mobility access, or
        accessibility support.
    actions:
    - type: send_message
      text: 'Please add the venue accessibility arrangements and contact here: ACCESSIBILITY_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.expect.programme
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks what happens during a service, the programme order,
        or what a typical gathering is like beyond the general expectation answer.
    actions:
    - type: send_message
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
      llm_gist: The person asks what language a service is in or whether interpretation
        is available.
    actions:
    - type: send_message
      text: 'Services are in SERVICE_LANGUAGE. Please add interpretation or translation
        support here: INTERPRETATION_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.expect.sensory
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether it will be loud, whether there is a quiet
        space, or about sensory needs.
    actions:
    - type: send_message
      text: 'Please add the sound, lighting, quiet-space, and sensory-support details
        here: SENSORY_SUPPORT_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.expect.participation
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether they will be forced to speak, join in, share
        personal details, or do anything uncomfortable.
    actions:
    - type: send_message
      text: 'You can take things at your own pace. Please add the approved participation
        and privacy reassurance here: PARTICIPATION_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.meet_someone
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks to talk to a real person, meet someone, get help from
        a leader, or be introduced to people outside a current live service.
    actions:
    - type: send_message
      text: 'Please add the approved contact path for meeting someone outside a live
        service here: COMMUNITY_CONTACT_PATH.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.socials
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks for The Zone social media, Instagram, Telegram channel,
        website, or online updates.
    actions:
    - type: send_message
      text: 'Please add the official social links here: INSTAGRAM_LINK, TELEGRAM_CHANNEL_LINK,
        WEBSITE_LINK.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.serve
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks how to volunteer, serve, help out, or join a team.
    actions:
    - type: send_message
      text: 'Please add the serving process, age requirements, and contact here: SERVING_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.contact_after
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks who they can contact after a service, after an event,
        or later in the week.
    actions:
    - type: send_message
      text: 'Please add the approved follow-up contact here: FOLLOW_UP_CONTACT.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.jesus
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks who Jesus is or what Christians believe about Jesus.
    actions:
    - type: send_message
      text: At NCC, we believe Jesus is the Son of God who came to reveal God’s love
        and give us new life through His death and resurrection.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.christianity
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks what Christians believe, what Christianity is, or
        what the church teaches.
    actions:
    - type: send_message
      text: 'Please add a short approved explanation of Christian belief here: CHRISTIANITY_EXPLANATION.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.follow_jesus
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks how to become a Christian, follow Jesus, or begin
        faith.
    actions:
    - type: send_message
      text: 'Please add the approved next-steps explanation and contact here: FOLLOW_JESUS_NEXT_STEPS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.bible_baptism
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks about reading the Bible, baptism, discipleship, or
        growing in faith.
    actions:
    - type: send_message
      text: 'Please add the approved Bible, baptism, and discipleship next steps here:
        FAITH_NEXT_STEPS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.prayer
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks for prayer, asks someone to pray with them, or asks
        how they can pray.
    actions:
    - type: send_message
      text: 'Please add the approved prayer response and contact path here: PRAYER_SUPPORT_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.faith.personal_question
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person has a personal faith question or asks to speak privately
        with someone about faith.
    actions:
    - type: send_message
      text: 'Please add the approved private faith-conversation contact path here:
        FAITH_CONVERSATION_CONTACT.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.support.personal
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person says they are struggling, anxious, lonely, grieving, or
        need someone to talk to, without describing immediate danger or self-harm.
    actions:
    - type: send_message
      text: 'Thank you for sharing that. Please add the approved non-emergency support
        contact and wording here: WELLBEING_SUPPORT_CONTACT.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.toilet
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks where the toilet or restroom is at Star Vista or The
        Zone venue.
    actions:
    - type: send_message
      text: The nearest toilets are on Level 4 beside the lifts. Ask a Zone team member
        if you would like someone to show you.
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.food_water
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks where to get food, drinks, water, refreshments, or
        whether food is provided.
    actions:
    - type: send_message
      text: 'Please add food, drink, water, refreshment, and cost details here: FOOD_AND_WATER_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.wifi_charging
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks about Wi-Fi, charging a phone, plugs, or power banks
        at the venue.
    actions:
    - type: send_message
      text: 'Please add the Wi-Fi and charging policy here: WIFI_AND_CHARGING_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.lost_property
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks about something they lost, found property, or the
        lost-and-found process.
    actions:
    - type: send_message
      text: 'Please add the lost-and-found location and contact here: LOST_PROPERTY_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.venue.medical
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person needs first aid, feels unwell, asks for medical help, or
        needs a Zone team member at the venue.
    actions:
    - type: send_message
      text: 'Please add the on-site medical-help and team-member instructions here:
        ON_SITE_HELP_INSTRUCTIONS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.policy.privacy
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks how their information is used, whether their details
        are private, or asks about data privacy.
    actions:
    - type: send_message
      text: 'Please add the approved privacy explanation and policy link here: PRIVACY_POLICY_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.policy.photos
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether photos or videos are taken, posted, or whether
        they can opt out.
    actions:
    - type: send_message
      text: 'Please add the approved photo, video, and opt-out policy here: MEDIA_POLICY_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.policy.parental_consent
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks whether they need parental consent, whether a parent
        can come, or about guardian requirements.
    actions:
    - type: send_message
      text: 'Please add the parental-consent and guardian policy here: PARENTAL_CONSENT_DETAILS.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.policy.safeguarding
    multi_intent_mode: answer
    trigger:
      type: message
      llm_gist: The person asks about behaviour rules, safeguarding, reporting a concern,
        or how The Zone keeps people safe.
    actions:
    - type: send_message
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
    - type: send_message
      text: 'I do not have an approved answer for that yet. Please add the best contact
        for unanswered questions here: GENERAL_QUESTION_CONTACT.'
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  - key: system.global.community.small_group
    trigger:
      type: message
      llm_gist: The person asks about joining a small group, cell group, connect group,
        or regular community group.
    actions:
    - type: send_message
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
      - type: send_message
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
    trigger: null
    actions:
    - type: send_message
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
    - type: send_message
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
          llm_gist: The person asks how to get to Star Performing Arts Centre or where
            Zone X is being held.
      actions:
      - type: send_message
        text: Zone X is held at The Star Performing Arts Centre, 1 Vista Exchange
          Green! Take the MRT to Buona Vista and follow the signs to The Star Vista.
          You'll meet our friendly welcome team in blue near the venue entrance to
          guide you.
      - type: send_buttons
        service_bound: true
        buttons:
        - button_id: zone_x.directions.open_map
          text: Open map
      next_flow_mode: ALLOW_MANY
      return_actions: []
      next_flows:
      - key: service.zone_x.directions.open_map
        trigger:
          type: button
          button_id: zone_x.directions.open_map
        actions:
        - type: send_message_fixed
          text: 'Map: {{ service.map_url }}'
        next_flow_mode: ONE_AND_ONCE_ONLY
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
          llm_gist: The person asks what Zone X or the service will be like, what
            will happen, what to wear, or whether they may come alone.
      actions:
      - type: send_message
        text: Come as you are. You can expect music, a message about Jesus, and time
          to meet other youths. It’s okay to come alone, sit quietly, or ask for someone
          to meet you before you enter.
      - type: send_buttons
        service_bound: true
        buttons:
        - button_id: zone_x.menu.connect
          text: Meet a friendly human
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
          llm_gist: The person wants to meet, talk to, or be connected with a friendly
            human at this service.
      actions:
      - type: send_message
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
          - type: send_message
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
            - type: send_buttons
              service_bound: true
              buttons:
              - button_id: zone_x.connect.not_responding
                text: "{{ matched_server.name }} is not responding"
            next_flow_mode: ALLOW_MANY
            return_actions: []
            next_flows:
            - key: service.zone_x.connect.join_group.not_responding
              trigger:
                type: button
                button_id: zone_x.connect.not_responding
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
                - type: send_message
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
            - type: send_buttons
              service_bound: true
              buttons:
              - button_id: zone_x.connect.not_responding
                text: "{{ matched_server.name }} is not responding"
            next_flow_mode: ALLOW_MANY
            return_actions: []
            next_flows:
            - key: service.zone_x.connect.join_me.not_responding
              trigger:
                type: button
                button_id: zone_x.connect.not_responding
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
                - type: send_message
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
          - type: send_message
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
          llm_gist: The person says they are actually attending another service or
            timing.
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
            - type: send_message
              text: Sorry, the service is over!
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
      - key: service.zone_x.change_service.none_available
        trigger:
          type: action_event
          event_key: service_attendance.none_available
        actions:
        - type: send_message
          text: There are no other ongoing services to switch to.
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows: []
  latecomer_flow:
    key: service.zone_x.latecomer
    trigger: null
    actions:
    - type: add_service_attendance
      attendance_status: latecomer
    - type: enter_service_checkpoint
      flow_key: service.zone_x.home
    - type: send_message
      text: Yes, you can still join Zone X. Service has started, so head to the venue
        entrance and ask a Zone team member to help you find a seat.
    - type: send_buttons
      service_bound: true
      buttons:
      - button_id: zone_x.menu.directions
        text: Get directions
      - button_id: zone_x.menu.connect
        text: Meet a friendly human
    next_flow_mode: ALLOW_MANY
    return_actions: []
    next_flows: []
  timestamps:
  - key: zone_x.marketing_starts
    occurs_at: '2026-09-27T10:00:00+08:00'
    audience: ALL_NBNCS
    root_flow:
      key: service.zone_x.timestamp.marketing
      trigger: null
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
      - type: send_message
        text: Would you like to know anything else about Zone X?
      next_flows:
      - key: service.zone_x.timestamp.marketing.expect
        trigger:
          type: button
          button_id: zone_x.marketing.what_to_expect
        actions:
        - type: send_message
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
      trigger: null
      actions:
      - type: send_message
        text: Zone X is tomorrow at 2:30 pm. Doors open at 1:30 pm at The Star Performing
          Arts Centre.
      - type: send_buttons
        service_bound: true
        buttons:
        - button_id: zone_x.one_day.directions
          text: Get directions
      next_flow_mode: CHECKPOINT
      return_actions:
      - type: send_message
        text: Anything else you’d like to know before Zone X?
      next_flows:
      - key: service.zone_x.timestamp.one_day_before.directions
        trigger:
          type: button
          button_id: zone_x.one_day.directions
        actions:
        - type: send_message_fixed
          text: 'Map: {{ service.map_url }}'
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows: []
  - key: zone_x.doors_open
    occurs_at: '2026-10-18T13:30:00+08:00'
    audience: ALL_NBNCS
    root_flow:
      key: service.zone_x.timestamp.doors_open
      trigger: null
      actions:
      - type: send_message
        text: Doors are open for Zone X. Are you here with us?
      - type: send_buttons
        service_bound: true
        buttons:
        - button_id: zone_x.attendance.here
          text: Check in to Zone X
          payload:
            service_key: zone_x_2026_10_18
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows:
      - key: service.zone_x.timestamp.doors_open.check_in
        trigger:
          type: button
          button_id: zone_x.attendance.here
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
          - type: send_message
            text: Sorry, the service is over!
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []
  - key: zone_x.service_starts
    occurs_at: '2026-10-18T14:30:00+08:00'
    audience: SERVICE_NBNCS
    root_flow:
      key: service.zone_x.timestamp.service_questions
      trigger: null
      actions:
      - type: send_message
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
      - type: send_message
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
            llm_gist: The person asks where the toilet or restroom is.
        actions:
        - type: send_message
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
            llm_gist: The person asks who Jesus is or what Christians believe about
              Jesus.
        actions:
        - type: send_message
          text: At NCC, we believe Jesus is the Son of God who came to reveal God’s
            love and give us new life through His death and resurrection. I can connect
            you with someone if you’d like to talk about this personally.
        - type: send_buttons
          service_bound: true
          buttons:
          - button_id: zone_x.menu.connect
            text: Talk to someone
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
        - type: send_message
          text: I don’t have an approved answer for that question, but I can connect
            you with someone who can talk with you.
        - type: send_buttons
          service_bound: true
          buttons:
          - button_id: zone_x.menu.connect
            text: Talk to someone
        next_flow_mode: ALLOW_MANY
        return_actions: []
        next_flows: []
  - key: zone_x.service_ends
    occurs_at: '2026-10-18T16:00:00+08:00'
    audience: ALL_SERVICE_ATTENDEES
    root_flow:
      key: service.zone_x.timestamp.after_service
      trigger: null
      actions:
      - type: send_message
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
      - type: send_message
        text: Would you like help with anything else before you go?
      next_flows:
      - key: service.zone_x.after.connect
        trigger:
          type: button
          button_id: zone_x.after.connect
        actions:
        - type: send_buttons
          service_bound: true
          text: I can introduce you to someone friendly.
          buttons:
          - button_id: zone_x.menu.connect
            text: Meet a friendly human
        next_flow_mode: ALLOW_MANY
        return_actions: []
        next_flows: []
      - key: service.zone_x.after.ask
        trigger:
          type: button
          button_id: zone_x.after.ask
        actions:
        - type: send_buttons
          service_bound: true
          text: A friendly human can help with your question.
          buttons:
          - button_id: zone_x.menu.connect
            text: Ask a friendly human
        next_flow_mode: ALLOW_MANY
        return_actions: []
        next_flows: []
  - key: zone_x.thank_you
    occurs_at: '2026-10-18T17:30:00+08:00'
    audience: ALL_SERVICE_ATTENDEES
    root_flow:
      key: service.zone_x.timestamp.thank_you
      trigger: null
      actions:
      - type: send_message
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
      trigger: null
      actions:
      - type: send_message
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
