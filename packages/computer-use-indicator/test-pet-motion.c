/* SPDX-License-Identifier: MIT
 * Actual cue engine, exercised without a display, desktop input or capture. */
#include <assert.h>
#include <stdio.h>
#include "pet-motion.h"

int main(void) {
    PetMotionState state = {0};
    PetMotionInput input = { .observation = OBS_ENABLED, .phase = PHASE_WAITING, .presentable = true };
    PetMotionDecision cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_IDLE && !cue.play);

    input.working_count = 1;
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_WORKING && cue.play);
    assert(!pet_motion_update(&state, &input).play);
    input.working_count = 2;
    assert(pet_motion_update(&state, &input).play);
    input.working_count = 1;
    assert(!pet_motion_update(&state, &input).play);

    input.kind = BUBBLE_QUESTION; input.item_id = "question-a";
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_ATTENTION && cue.play);
    assert(!pet_motion_update(&state, &input).play);
    input.phase = PHASE_OBSERVING;
    assert(pet_motion_update(&state, &input).pose == POSE_READING);
    input.phase = PHASE_WAITING;
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_ATTENTION && !cue.play && cue.settle);

    input.kind = BUBBLE_APPROVAL; /* A kind change is not the same interaction. */
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_WAITING && cue.play);
    input.kind = BUBBLE_RESULT; input.item_id = "answer-a";
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_SUCCESS && cue.play);
    assert(!pet_motion_update(&state, &input).play);
    input.item_id = "answer-b";
    assert(pet_motion_update(&state, &input).play);
    input.kind = BUBBLE_QUESTION; input.item_id = "question-b";
    assert(pet_motion_update(&state, &input).play);
    input.kind = BUBBLE_RESULT; input.item_id = "answer-b";
    assert(!pet_motion_update(&state, &input).play);

    input.observation = OBS_DISABLED;
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_PRIVATE && pet_native_eye_cover(cue.pose));
    input.observation = OBS_UNKNOWN;
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_UNKNOWN && pet_native_eye_cover(cue.pose));
    input.observation = OBS_ENABLED;
    input.kind = BUBBLE_NONE; input.item_id = NULL; /* Dismiss does not manufacture a result. */
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_WORKING && !cue.play);

    input.kind = BUBBLE_QUESTION; input.item_id = "reduced-question"; input.reduced_motion = true;
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_ATTENTION && !cue.play && cue.settle);
    input.reduced_motion = false;
    assert(!pet_motion_update(&state, &input).play);

    input.kind = BUBBLE_NONE; input.item_id = NULL; input.working_count = 0;
    input.phase = PHASE_ACTIVE;
    assert(pet_motion_update(&state, &input).play);
    input.phase = PHASE_OBSERVING;
    assert(pet_motion_update(&state, &input).pose == POSE_READING);
    input.phase = PHASE_ACTIVE;
    cue = pet_motion_update(&state, &input);
    assert(cue.pose == POSE_WORKING && !cue.play);
    input.phase = PHASE_WAITING;
    assert(pet_motion_update(&state, &input).pose == POSE_IDLE);
    input.phase = PHASE_ACTIVE;
    assert(pet_motion_update(&state, &input).play);

    input.phase = PHASE_WAITING; input.presentable = false;
    input.kind = BUBBLE_QUESTION; input.item_id = "hidden-question";
    assert(!pet_motion_update(&state, &input).play);
    input.presentable = true;
    assert(pet_motion_update(&state, &input).play);
    assert(!pet_native_eye_cover(POSE_READING));
    puts("PASS: event-bound work, item identity, reading resume, privacy, dismiss and reduced motion");
    return 0;
}
