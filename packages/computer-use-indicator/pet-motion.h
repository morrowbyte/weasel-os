/* SPDX-License-Identifier: MIT
 * Presentation cues only: no capture, input, permission, timers or task leases.
 * The legacy sprite row names remain ABI-compatible. Row 4 waits for approval,
 * row 5 asks a question, and row 6 offers an answer without asserting success.
 */
#ifndef WEASEL_PET_MOTION_H
#define WEASEL_PET_MOTION_H

#include <stdbool.h>
#include <string.h>

typedef enum {
    POSE_IDLE, POSE_WORKING, POSE_READING, POSE_PRIVATE,
    POSE_WAITING, POSE_ATTENTION, POSE_SUCCESS, POSE_UNKNOWN
} PetPose;
typedef enum { PHASE_UNKNOWN, PHASE_ACTIVE, PHASE_OBSERVING,
               PHASE_WAITING, PHASE_QUEUED } Phase;
typedef enum { OBS_UNKNOWN, OBS_ENABLED, OBS_DISABLED } Observation;
typedef enum { BUBBLE_NONE, BUBBLE_APPROVAL, BUBBLE_QUESTION, BUBBLE_RESULT } BubbleKind;

#define PET_CUE_HISTORY 20u
#define PET_CUE_ID_BYTES 129u

typedef struct {
    Observation observation;
    bool observation_active;
    Phase phase;
    BubbleKind kind;
    const char *item_id;
    unsigned working_count;
    bool presentable, reduced_motion;
} PetMotionInput;

typedef struct {
    char id[PET_CUE_ID_BYTES];
    BubbleKind kind;
} PetSeenCue;

typedef struct {
    bool initialized, reduced_motion, actor_work_active, work_pending;
    unsigned working_count, next_seen;
    PetPose pose;
    PetSeenCue seen[PET_CUE_HISTORY];
} PetMotionState;

typedef struct {
    PetPose pose;
    bool play, changed_pose, settle;
} PetMotionDecision;

static inline bool pet_native_eye_cover(PetPose pose) {
    return pose == POSE_PRIVATE || pose == POSE_UNKNOWN;
}

static inline PetPose pet_pose_for(const PetMotionInput *input) {
    if (input->observation == OBS_DISABLED) return POSE_PRIVATE;
    if (input->observation == OBS_UNKNOWN) return POSE_UNKNOWN;
    if (input->observation_active || input->phase == PHASE_OBSERVING) return POSE_READING;
    if (input->item_id && *input->item_id) {
        if (input->kind == BUBBLE_APPROVAL) return POSE_WAITING;
        if (input->kind == BUBBLE_QUESTION) return POSE_ATTENTION;
        if (input->kind == BUBBLE_RESULT) return POSE_SUCCESS;
    }
    if (input->phase == PHASE_ACTIVE || input->working_count) return POSE_WORKING;
    /* Queue counts and engine waiting are not requests for user approval. */
    return POSE_IDLE;
}

static inline bool pet_cue_is_new(PetMotionState *state, const PetMotionInput *input) {
    for (unsigned i = 0; i < PET_CUE_HISTORY; ++i)
        if (state->seen[i].kind == input->kind && !strcmp(state->seen[i].id, input->item_id))
            return false;
    PetSeenCue *slot = &state->seen[state->next_seen];
    size_t length = strlen(input->item_id);
    if (length >= sizeof slot->id) length = sizeof slot->id - 1;
    memcpy(slot->id, input->item_id, length);
    slot->id[length] = '\0';
    slot->kind = input->kind;
    state->next_seen = (state->next_seen + 1) % PET_CUE_HISTORY;
    return true;
}

static inline PetMotionDecision pet_motion_update(PetMotionState *state, const PetMotionInput *input) {
    PetMotionDecision decision = { .pose = pet_pose_for(input) };
    decision.changed_pose = !state->initialized || state->pose != decision.pose;
    decision.settle = decision.changed_pose || (input->reduced_motion && !state->reduced_motion);
    if (input->working_count > state->working_count) state->work_pending = true;
    if (input->phase == PHASE_ACTIVE) {
        if (!state->actor_work_active) state->work_pending = true;
        state->actor_work_active = true;
    } else if (input->phase != PHASE_OBSERVING) {
        state->actor_work_active = false;
    }
    if (!input->working_count && input->phase != PHASE_ACTIVE && input->phase != PHASE_OBSERVING)
        state->work_pending = false;

    if (input->presentable) {
        if (decision.pose == POSE_ATTENTION || decision.pose == POSE_WAITING || decision.pose == POSE_SUCCESS)
            decision.play = pet_cue_is_new(state, input);
        else if (decision.pose == POSE_WORKING) {
            decision.play = state->work_pending;
            state->work_pending = false;
        } else if (decision.pose == POSE_READING || pet_native_eye_cover(decision.pose)) {
            decision.play = decision.changed_pose;
        }
    }
    if (input->reduced_motion) decision.play = false;
    state->pose = decision.pose;
    state->working_count = input->working_count;
    state->reduced_motion = input->reduced_motion;
    state->initialized = true;
    return decision;
}

#endif
