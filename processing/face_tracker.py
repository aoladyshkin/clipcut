# -*- coding: utf-8 -*- 

import cv2
import numpy as np
from moviepy.editor import vfx
from config import HAARCASCADE_FRONTALFACE_DEFAULT, HAARCASCADE_PROFILEFACE

def get_box_center(box):
    x, y, w, h = box
    return (x + w/2, y + h/2)

def distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5

def create_face_tracked_clip(main_clip_raw, target_height, target_width):
    """
    Creates a clip with smooth face tracking, only moving the frame when
    the face nears the edge of the visible area.
    """
    main_clip_resized = main_clip_raw.resize(height=target_height)
    
    if main_clip_resized.w <= target_width:
        return main_clip_resized

    try:
        face_cascade = cv2.CascadeClassifier(HAARCASCADE_FRONTALFACE_DEFAULT)
        profile_cascade = cv2.CascadeClassifier(HAARCASCADE_PROFILEFACE)
    except Exception as e:
        print(f"Could not load face cascade model(s): {e}. Falling back to center crop.")
        return main_clip_resized.fx(vfx.crop, x_center=main_clip_resized.w / 2, width=target_width)

    # 1. Analyze face positions and sizes throughout the clip
    processing_fps = 15
    timestamps = np.arange(0, main_clip_resized.duration, 1/processing_fps)
    face_boxes = []
    tracked_face_box = None

    for t in timestamps:
        frame = main_clip_resized.get_frame(t)
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        
        faces_frontal = face_cascade.detectMultiScale(gray, 1.1, 6, minSize=(100, 100))
        faces_profile = profile_cascade.detectMultiScale(gray, 1.1, 6, minSize=(100, 100))
        gray_flipped = cv2.flip(gray, 1)
        faces_profile_flipped = profile_cascade.detectMultiScale(gray_flipped, 1.1, 6, minSize=(100, 100))
        
        all_faces = []
        if len(faces_frontal) > 0: all_faces.extend(list(faces_frontal))
        if len(faces_profile) > 0: all_faces.extend(list(faces_profile))
        if len(faces_profile_flipped) > 0:
            for (x, y, w, h) in faces_profile_flipped:
                all_faces.append((gray.shape[1] - x - w, y, w, h))

        faces = np.array(all_faces)
        
        # Determine if we should be in group mode for this frame (temporarily disabled)
        use_group_logic = False
        # if len(faces) > 1:
        #     x_min = min(faces[:, 0])
        #     x_max = max(faces[:, 0] + faces[:, 2])
        #     group_width = x_max - x_min
        #     if group_width < target_width * 0.9:
        #         use_group_logic = True

        current_face_box = None
        if use_group_logic:
            # Calculate the ideal group box for the current frame
            y_min = min(faces[:, 1])
            y_max = max(faces[:, 1] + faces[:, 3])
            current_group_box = np.array([x_min, y_min, group_width, y_max - y_min])

            # Smoothly update the tracked_face_box towards the new group box
            if tracked_face_box is None:
                # First time entering group mode (or after reset)
                tracked_face_box = current_group_box
            else:
                # Smooth the transition to avoid jumps
                alpha = 0.4 
                tracked_face_box = (1 - alpha) * tracked_face_box + alpha * current_group_box
            
            current_face_box = tracked_face_box

        elif len(faces) > 0:
            # Fallback to original single-face tracking logic
            if tracked_face_box is None:
                tracked_face_box = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)[0]
            else:
                previous_center = get_box_center(tracked_face_box)
                closest_face = min(faces, key=lambda f: distance(get_box_center(f), previous_center))
                max_allowed_distance = tracked_face_box[2] * 1.5
                if distance(get_box_center(closest_face), previous_center) < max_allowed_distance:
                    tracked_face_box = closest_face
                # else:
                #     # If the closest face is too far, we assume it's a false positive
                #     # and we don't update the tracker. The interpolation logic will hold
                #     # the last known position.
                #     tracked_face_box = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)[0]
            current_face_box = tracked_face_box
        else:
            # No faces detected
            tracked_face_box = None
        
        face_boxes.append(current_face_box)

    # 2. Fill in missing face boxes (hold last known box)
    last_known_box = None
    interp_face_boxes = []
    for box in face_boxes:
        if box is not None:
            interp_face_boxes.append(box)
            last_known_box = box
        else:
            interp_face_boxes.append(last_known_box)
    
    if not any(b is not None for b in interp_face_boxes):
        print("No faces found in the clip. Falling back to center crop.")
        return main_clip_resized.fx(vfx.crop, x_center=main_clip_resized.w / 2, width=target_width)
    
    first_valid_box_index = -1
    for i, box in enumerate(interp_face_boxes):
        if box is not None:
            first_valid_box_index = i
            break
    
    if first_valid_box_index != -1:
        for i in range(first_valid_box_index):
            interp_face_boxes[i] = interp_face_boxes[first_valid_box_index]


    # 3. Generate the smoothed crop path with dead zone logic
    crop_path_x = []
    crop_half_width = target_width / 2
    crop_x_center = main_clip_resized.w / 2
    target_crop_x_center = main_clip_resized.w / 2
    smoothing_factor = 0.1

    for box in interp_face_boxes:
        if box is None:
            # Should not happen due to filling logic, but as a safeguard
            crop_path_x.append(crop_x_center)
            continue

        face_center_x, _ = get_box_center(box)
        face_width = box[2]
        
        visible_left = crop_x_center - crop_half_width
        visible_right = crop_x_center + crop_half_width
        buffer = face_width * 0.5

        if not (visible_left + buffer < face_center_x < visible_right - buffer):
            target_crop_x_center = face_center_x
        
        crop_x_center = (smoothing_factor * target_crop_x_center) + ((1 - smoothing_factor) * crop_x_center)
        crop_path_x.append(crop_x_center)

    # 4. Create a function that returns the crop center for any time t
    min_x = crop_half_width
    max_x = main_clip_resized.w - crop_half_width
    final_smoothed_x = np.clip(crop_path_x, min_x, max_x)

    def get_crop_x(t):
        return np.interp(t, timestamps, final_smoothed_x)

    # 5. Apply the animated crop using .fl() for compatibility
    def time_varying_crop(gf, t):
        frame = gf(t)
        center_x = get_crop_x(t)
        
        # Calculate integer bounds for the crop, ensuring constant width
        x1 = int(round(center_x - target_width / 2))
        x2 = x1 + target_width
        
        # Ensure crop is within frame bounds
        h, w, c = frame.shape
        x1 = max(0, x1)
        x2 = min(w, x2)
        # Adjust x1 if x2 was clipped
        x1 = x2 - target_width

        return frame[:, x1:x2]

    final_video = main_clip_resized.fl(time_varying_crop)
    final_video.audio = main_clip_raw.audio
    return final_video

