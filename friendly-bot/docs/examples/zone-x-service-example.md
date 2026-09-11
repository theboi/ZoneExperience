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

The YAML below is the normative human-readable representation of the first development service. It shows the complete Zone X service, including its service-global checkpoint, latecomer flow, and every configured development timestamp. It also shows the excerpt of the system-global root needed to understand safety and “never mind”; unrelated onboarding, login, and basic-information branches are intentionally omitted. Implementation must provide an equivalent JSON seed, and published flow versions are stored as PostgreSQL `JSONB`.

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
system_global_root_excerpt:
  key: system.global
  trigger: null
  actions: []
  next_flow_mode: CHECKPOINT
  return_actions:
    - type: send_message
      text: "What would you like help with?"
  next_flows:
    - key: system.global.never_mind
      trigger:
        type: message
        llm_gist: >-
          The person wants to stop, cancel, go back, leave the current topic,
          or says never mind.
      actions:
        - type: return_to_nearest_checkpoint
      next_flow_mode: ONE_AND_ONCE_ONLY
      return_actions: []
      next_flows: []

    - key: system.global.safety
      trigger:
        type: message
        llm_gist: >-
          The person may be in immediate danger, is considering suicide or
          self-harm, is being abused, or urgently needs a safe responsible adult.
      actions:
        - type: send_message
          text: >-
            Thank you for telling me. You do not have to handle this alone.
            I’m finding a trusted person who can support you now.
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
              text: >-
                Urgent support request from {{ user.name }}. Please contact them
                as soon as possible. Only information the person provided in
                this flow may be included.
            - type: share_human_contact
              text: >-
                {{ matched_human.name }} is a trusted person you can contact now:
                {{ matched_human.telegram_url }}
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []
        - key: system.global.safety.no_responder
          trigger:
            type: action_event
            event_key: safety_match.not_found
          actions:
            - type: send_message
              text: >-
                I can’t reach a trusted person through the bot right now. If you
                may be in immediate danger, call emergency services or go to a
                trusted adult near you now.
            - type: notify_all_admins
              severity: urgent
              safe_summary: "A safety connection request has no eligible responder."
            - type: mark_safety_request_pending
          next_flow_mode: ONE_AND_ONCE_ONLY
          return_actions: []
          next_flows: []

service:
  key: zone_x_2026_10_18
  name: Zone X
  timezone: Asia/Singapore
  highkey: true
  doors_open_at: "2026-10-18T13:30:00+08:00"
  doors_close_at: "2026-10-18T14:20:00+08:00"
  service_starts_at: "2026-10-18T14:30:00+08:00"
  service_ends_at: "2026-10-18T16:00:00+08:00"
  interaction_ends_at: "2026-10-18T18:00:00+08:00"

  service_global_root:
    key: service.zone_x.home
    trigger: null
    actions:
      - type: send_message
        text: "You’re checked in for Zone X. What would you like to do?"
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
        text: "Is there anything else you’d like help with at Zone X?"
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
              llm_gist: >-
                The person asks how to get to Star Performing Arts Centre or
                where Zone X is being held.
        actions:
          - type: send_message
            text: >-
              Zone X is at The Star Performing Arts Centre, 1 Vista Exchange
              Green. Take the MRT to Buona Vista and follow signs to The Star
              Vista. Our team will be near the venue entrance to guide you.
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
              - type: send_message
                text: "Map: {{ service.map_url }}"
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []

      - key: service.zone_x.what_to_expect
        trigger:
          type: any_of
          triggers:
            - type: button
              button_id: zone_x.menu.expect
            - type: message
              llm_gist: >-
                The person asks what Zone X or the service will be like, what
                will happen, what to wear, or whether they may come alone.
        actions:
          - type: send_message
            text: >-
              Come as you are. You can expect music, a message about Jesus, and
              time to meet other youths. It’s okay to come alone, sit quietly,
              or ask for someone to meet you before you enter.
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
              llm_gist: >-
                The person wants to meet, talk to, or be connected with a
                friendly human at this service.
        actions:
          - type: send_message
            text: >-
              What is one thing that interests you? Nothing is a valid answer too!
        next_flow_mode: ONE_AND_ONCE_ONLY
        return_actions: []
        next_flows:
          - key: service.zone_x.connect.capture_interest
            trigger:
              type: message
              llm_gist: >-
                The person answers the question about an interest or says they
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
                    text: >-
                      I found {{ matched_server.name }} from
                      {{ matched_server.cg_name }}. How would you like to meet?
                  - type: send_buttons
                    service_bound: true
                    buttons:
                      - button_id: zone_x.connect.join_group
                        text: "Join {{ matched_server.name }}"
                      - button_id: zone_x.connect.join_me
                        text: "Ask {{ matched_server.name }} to join me"
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
                        text: >-
                          {{ user.name }} is at {{ service.name }} and would like
                          to join you. Their interest:
                          {{ human_match_request.interest }}. They may contact you
                          on Telegram.
                      - type: share_human_contact
                        text: >-
                          {{ matched_server.name }} is expecting you. Message them
                          here: {{ matched_server.telegram_url }}
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
                            text: >-
                              {{ user.name }} reported that they could not reach
                              you. Their connection request will be reassigned.
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
                                text: >-
                                  {{ user.name }} is at {{ service.name }} and
                                  would like to join you. Their interest:
                                  {{ human_match_request.interest }}. They may
                                  contact you on Telegram.
                              - type: share_human_contact
                                text: >-
                                  Try {{ matched_server.name }} instead:
                                  {{ matched_server.telegram_url }}
                            next_flow_mode: ONE_AND_ONCE_ONLY
                            return_actions: []
                            next_flows: []
                          - key: service.zone_x.connect.join_group.rematch_not_found
                            trigger:
                              type: action_event
                              event_key: human_match.not_found
                            actions:
                              - type: send_message
                                text: >-
                                  Sorry, nobody else is available to meet right
                                  now. Please speak to a Zone team member at the
                                  venue.
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
                        text: >-
                          {{ user.name }} is at {{ service.name }} and would like
                          you to join them. Their interest:
                          {{ human_match_request.interest }}. They may contact you
                          on Telegram.
                      - type: share_human_contact
                        text: >-
                          {{ matched_server.name }} will come and meet you. Message
                          them here so you can find each other:
                          {{ matched_server.telegram_url }}
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
                            text: >-
                              {{ user.name }} reported that they could not reach
                              you. Their connection request will be reassigned.
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
                                text: >-
                                  {{ user.name }} is at {{ service.name }} and
                                  would like you to join them. Their interest:
                                  {{ human_match_request.interest }}. They may
                                  contact you on Telegram.
                              - type: share_human_contact
                                text: >-
                                  Try {{ matched_server.name }} instead:
                                  {{ matched_server.telegram_url }}
                            next_flow_mode: ONE_AND_ONCE_ONLY
                            return_actions: []
                            next_flows: []
                          - key: service.zone_x.connect.join_me.rematch_not_found
                            trigger:
                              type: action_event
                              event_key: human_match.not_found
                            actions:
                              - type: send_message
                                text: >-
                                  Sorry, nobody else is available to meet right
                                  now. Please speak to a Zone team member at the
                                  venue.
                            next_flow_mode: ONE_AND_ONCE_ONLY
                            return_actions: []
                            next_flows: []

              - key: service.zone_x.connect.no_match
                trigger:
                  type: action_event
                  event_key: human_match.not_found
                actions:
                  - type: send_message
                    text: >-
                      Sorry, nobody is available to meet right now. Please try
                      again later or speak to a Zone team member at the venue.
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
              llm_gist: >-
                The person says they are actually attending another service or
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
                        text: "Sorry, the service is over!"
                    next_flow_mode: ONE_AND_ONCE_ONLY
                    return_actions: []
                    next_flows: []
          - key: service.zone_x.change_service.none_available
            trigger:
              type: action_event
              event_key: service_attendance.none_available
            actions:
              - type: send_message
                text: "There are no other ongoing services to switch to."
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
        text: >-
          Yes, you can still join Zone X. Service has started, so head to the
          venue entrance and ask a Zone team member to help you find a seat.
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
      occurs_at: "2026-09-27T10:00:00+08:00"
      audience: ALL_NBNCS
      root_flow:
        key: service.zone_x.timestamp.marketing
        trigger: null
        actions:
          - type: send_photo
            asset_key: zone_x_poster_2026
            caption: >-
              Zone X is happening on 18 October at The Star Performing Arts
              Centre. You can come alone—we’ll help you meet someone friendly.
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
            text: "Would you like to know anything else about Zone X?"
        next_flows:
          - key: service.zone_x.timestamp.marketing.expect
            trigger:
              type: button
              button_id: zone_x.marketing.what_to_expect
            actions:
              - type: send_message
                text: >-
                  Come as you are. There will be music, a message about Jesus,
                  and friendly people who can sit with you.
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []
          - key: service.zone_x.timestamp.marketing.directions
            trigger:
              type: button
              button_id: zone_x.marketing.directions
            actions:
              - type: send_message
                text: >-
                  Take the MRT to Buona Vista and follow signs to The Star Vista.
                  Map: {{ service.map_url }}
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []

    - key: zone_x.one_day_before
      occurs_at: "2026-10-17T14:30:00+08:00"
      audience: ALL_NBNCS
      root_flow:
        key: service.zone_x.timestamp.one_day_before
        trigger: null
        actions:
          - type: send_message
            text: >-
              Zone X is tomorrow at 2:30 pm. Doors open at 1:30 pm at The Star
              Performing Arts Centre.
          - type: send_buttons
            service_bound: true
            buttons:
              - button_id: zone_x.one_day.directions
                text: Get directions
        next_flow_mode: CHECKPOINT
        return_actions:
          - type: send_message
            text: "Anything else you’d like to know before Zone X?"
        next_flows:
          - key: service.zone_x.timestamp.one_day_before.directions
            trigger:
              type: button
              button_id: zone_x.one_day.directions
            actions:
              - type: send_message
                text: "Map: {{ service.map_url }}"
            next_flow_mode: ONE_AND_ONCE_ONLY
            return_actions: []
            next_flows: []

    - key: zone_x.doors_open
      occurs_at: "2026-10-18T13:30:00+08:00"
      audience: ALL_NBNCS
      root_flow:
        key: service.zone_x.timestamp.doors_open
        trigger: null
        actions:
          - type: send_message
            text: "Doors are open for Zone X. Are you here with us?"
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
                    text: "Sorry, the service is over!"
                next_flow_mode: ONE_AND_ONCE_ONLY
                return_actions: []
                next_flows: []

    - key: zone_x.service_starts
      occurs_at: "2026-10-18T14:30:00+08:00"
      audience: SERVICE_NBNCS
      root_flow:
        key: service.zone_x.timestamp.service_questions
        trigger: null
        actions:
          - type: send_message
            text: "Service has started. You can ask a question here at any time."
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
            text: "Is there anything else you’d like to ask about service?"
          - type: send_buttons
            service_bound: true
            buttons:
              - button_id: zone_x.service.toilet
                text: Where is the toilet?
              - button_id: zone_x.service.who_is_jesus
                text: Who is Jesus?
        next_flows:
          - key: service.zone_x.service.toilet
            trigger:
              type: any_of
              triggers:
                - type: button
                  button_id: zone_x.service.toilet
                - type: message
                  llm_gist: >-
                    The person asks where the toilet or restroom is.
            actions:
              - type: send_message
                text: >-
                  The nearest toilets are on Level 4 beside the lifts. Ask a Zone
                  team member if you’d like someone to show you.
            next_flow_mode: ALLOW_MANY
            return_actions: []
            next_flows: []

          - key: service.zone_x.service.who_is_jesus
            trigger:
              type: any_of
              triggers:
                - type: button
                  button_id: zone_x.service.who_is_jesus
                - type: message
                  llm_gist: >-
                    The person asks who Jesus is or what Christians believe about
                    Jesus.
            actions:
              - type: send_message
                text: >-
                  At NCC, we believe Jesus is the Son of God who came to reveal
                  God’s love and give us new life through His death and
                  resurrection. I can connect you with someone if you’d like to
                  talk about this personally.
              - type: send_buttons
                service_bound: true
                buttons:
                  - button_id: zone_x.menu.connect
                    text: Talk to someone
            next_flow_mode: ALLOW_MANY
            return_actions: []
            next_flows: []

          - key: service.zone_x.service.unknown_question
            trigger:
              type: message
              llm_gist: >-
                The person asks a genuine question about service that is not
                covered by a more specific open question flow.
            actions:
              - type: send_message
                text: >-
                  I don’t have an approved answer for that question, but I can
                  connect you with someone who can talk with you.
              - type: send_buttons
                service_bound: true
                buttons:
                  - button_id: zone_x.menu.connect
                    text: Talk to someone
            next_flow_mode: ALLOW_MANY
            return_actions: []
            next_flows: []

    - key: zone_x.service_ends
      occurs_at: "2026-10-18T16:00:00+08:00"
      audience: ALL_SERVICE_ATTENDEES
      root_flow:
        key: service.zone_x.timestamp.after_service
        trigger: null
        actions:
          - type: send_message
            text: "Service has ended. What would you like to do next?"
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
            text: "Would you like help with anything else before you go?"
        next_flows:
          - key: service.zone_x.after.connect
            trigger:
              type: button
              button_id: zone_x.after.connect
            actions:
              - type: send_buttons
                service_bound: true
                text: "I can introduce you to someone friendly."
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
                text: "A friendly human can help with your question."
                buttons:
                  - button_id: zone_x.menu.connect
                    text: Ask a friendly human
            next_flow_mode: ALLOW_MANY
            return_actions: []
            next_flows: []

    - key: zone_x.thank_you
      occurs_at: "2026-10-18T17:30:00+08:00"
      audience: ALL_SERVICE_ATTENDEES
      root_flow:
        key: service.zone_x.timestamp.thank_you
        trigger: null
        actions:
          - type: send_message
            text: >-
              Thank you for coming to Zone X today. We’re glad you were here.
              You can still use the service options until 6:00 pm.
        next_flow_mode: ALLOW_MANY
        return_actions: []
        next_flows: []

    - key: zone_x.interaction_ends
      occurs_at: "2026-10-18T18:00:00+08:00"
      audience: ALL_SERVICE_ATTENDEES
      root_flow:
        key: service.zone_x.timestamp.interaction_ends
        trigger: null
        actions:
          - type: send_message
            text: >-
              Zone X has ended, but you can still ask for directions to Star or
              learn more about NCC here anytime.
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

If any assumption above is wrong, changing it may affect the product specification or architecture masterplan—not just this example.
