# Task 3 Report: Per-Turn Main-Agent Routing

## Status

Implemented without commits or branches. Existing Task 1 contracts and Task 2 persistence files were preserved.

## Files

- Added app/services/main_agent.py.
- Modified app/services/planning.py.
- Modified app/schemas/planning.py.
- Extended tests/test_main_agent_routing.py.

## Red Evidence

Initial required command:

    .venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py -q

Output:

    =================================== ERRORS ====================================
    ______________ ERROR collecting tests/test_main_agent_routing.py ______________
    ImportError while importing test module 'D:\Desktop\project\Trip\tests\test_main_agent_routing.py'.
    tests\test_main_agent_routing.py:3: in <module>
        from app.services.main_agent import MainAgentService
    E   ModuleNotFoundError: No module named 'app.services.main_agent'
    =========================== short test summary info ===========================
    ERROR tests/test_main_agent_routing.py
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 error in 1.36s

After adding the service, the direct-planning prefill test exposed a second red state:

    .F................
    FAILED tests/test_main_agent_routing.py::test_direct_plan_request_opens_prefilled_form
    KeyError: 'destination'
    1 failed, 17 passed, 2 warnings in 28.75s

The extractor missed "帮我规划一次成都旅行" and tried its remote fallback despite MainAgentService(use_llm=False).

## Green Evidence

Focused routing and requirement command:

    .venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py tests/test_phase1_planning_contracts.py tests/test_phase5_generate_first.py -q

Output:

    ..................                                                       [100%]
    18 passed, 2 warnings in 21.77s

Required final command:

    .venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py tests/test_phase1_planning_contracts.py -q

Output:

    ...........                                                              [100%]
    11 passed, 1 warning in 15.06s

Additional checks:

    .venv\Scripts\python.exe -m compileall -q app
    git diff --check -- app/services/main_agent.py app/services/planning.py app/schemas/planning.py tests/test_main_agent_routing.py

Both commands exited with status 0. The diff check only emitted pre-existing CRLF conversion warnings for app/schemas/planning.py and app/services/planning.py.

## Self-Review

- Deterministic rules route explicit planning requests to the form, preserve only current-message destination/date/day values for prefill, and never invent a date or duration.
- Affirmations open the form only when the recent assistant context contains the exact proactive offer.
- Open questions take precedence over historical tool slots and route to RAG; destination recommendation is a distinct action.
- Ambiguous turns use structured MainAgentDecision output only when enabled, with an instruction that historical slots cannot establish planning intent. Failed or disabled LLM routing returns direct_response.
- Requirement extraction now supports the direct planning phrase used by the form prefill test and can disable its LLM fallback for deterministic callers.
- Removed DEFAULT_DAYS, DEFAULT_DEPARTURE_OFFSET_DAYS, and to_requirement_with_defaults; strict to_requirement() remains the sole conversion method.

## Concerns

- Per the task boundary, app/api/v1/chat.py was not changed. It still references the removed to_requirement_with_defaults() method in the pre-existing Task 2 flow. Task 5 replaces that chat path with the main-agent/tool flow; until then, invoking the legacy supervisor stream would fail at that call site.
- The focused suites emit existing third-party deprecation warnings from jieba and LangGraph. No new test warnings were introduced.

## Review Fix Evidence

### Red

Command:

    .venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py -q

Output:

    ..F..FFFF.FF..F
    FAILED tests/test_main_agent_routing.py::test_explicit_planning_takes_precedence_over_open_question_markers
    FAILED tests/test_main_agent_routing.py::test_destination_recommendation_covers_uncertain_destination_requests[帮我推荐适合亲子游的地方]
    FAILED tests/test_main_agent_routing.py::test_destination_recommendation_covers_uncertain_destination_requests[不知道去哪]
    FAILED tests/test_main_agent_routing.py::test_destination_recommendation_covers_uncertain_destination_requests[去哪玩比较好]
    FAILED tests/test_main_agent_routing.py::test_known_city_attraction_question_is_not_destination_recommendation
    FAILED tests/test_main_agent_routing.py::test_affirmation_requires_latest_exact_proactive_offer[context0]
    FAILED tests/test_main_agent_routing.py::test_affirmation_requires_latest_exact_proactive_offer[context1]
    FAILED tests/test_main_agent_routing.py::test_enabled_llm_without_key_does_not_attempt_routing_model
    8 failed, 7 passed in 15.97s

### Fixes

- Explicit planning checks before recommendation and open-question rules.
- An affirmation now checks only the latest non-system context item and requires its assistant content to exactly equal the normalized proactive offer.
- Destination recommendation recognizes no-destination phrasing and generic recommendation requests, while known-city attraction, family, and recommendation questions stay on the open-question route.
- Form prefill always calls RequirementExtractor with use_llm=False, so it only uses deterministic extraction and does not fabricate missing fields.
- LLM routing is enabled only when it is not explicitly disabled and a DashScope key is configured. use_llm=True without a key returns direct_response without calling get_llm.

### Green

Routing regressions:

    .venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py -q
    ...............                                                          [100%]
    15 passed in 12.55s

Task 3 suite:

    .venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py tests/test_phase1_planning_contracts.py tests/test_phase5_generate_first.py -q
    ...........................                                              [100%]
    27 passed, 2 warnings in 22.69s

Additional checks:

    .venv\Scripts\python.exe -m compileall -q app
    git diff --check -- app/services/main_agent.py app/services/planning.py app/schemas/planning.py tests/test_main_agent_routing.py

Both commands exited with status 0. The diff check only emitted CRLF conversion warnings for pre-existing modified planning files.
