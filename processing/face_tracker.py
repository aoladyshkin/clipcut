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
    import cv2
    import numpy as np
    from moviepy.editor import vfx

    main_clip_resized = main_clip_raw.resize(height=target_height)
    
    if main_clip_resized.w <= target_width:
        return main_clip_resized

    try:
        face_cascade = cv2.CascadeClassifier(HAARCASCADE_FRONTALFACE_DEFAULT)
        profile_cascade = cv2.CascadeClassifier(HAARCASCADE_PROFILEFACE)
    except Exception as e:
        print(f"Could not load face cascade model(s): {e}. Falling back to center crop.")
        return main_clip_resized.fx(vfx.crop, x_center=main_clip_resized.w / 2, width=target_width)

    processing_fps = 15
    timestamps = np.arange(0, main_clip_resized.duration, 1/processing_fps)
    face_boxes = []
    tracked_face_box = None

    for t in timestamps:
        frame = main_clip_resized.get_frame(t)
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

        faces_frontal = face_cascade.detectMultiScale(gray, 1.1, 8, minSize=(100, 100))
        faces_profile = profile_cascade.detectMultiScale(gray, 1.1, 8, minSize=(100, 100))

        gray_flipped = cv2.flip(gray, 1)
        faces_profile_flipped = profile_cascade.detectMultiScale(gray_flipped, 1.1, 8, minSize=(100, 100))

        all_faces = []
        if len(faces_frontal) > 0: all_faces.extend(faces_frontal)
        if len(faces_profile) > 0: all_faces.extend(faces_profile)
        if len(faces_profile_flipped) > 0:
            for (x, y, w, h) in faces_profile_flipped:
                all_faces.append((gray.shape[1] - x - w, y, w, h))

        faces = np.array(all_faces)

        current_face_box = None
        is_hard_cut = False

        # -------------------------------
        # НОВАЯ ЛОГИКА ПЛАВНЫХ/РЕЗКИХ ПЕРЕМЕЩЕНИЙ
        # -------------------------------
        if tracked_face_box is not None and len(faces) > 0:
            previous_center = get_box_center(tracked_face_box)
            previous_width = tracked_face_box[2]

            closest_face = min(
                faces,
                key=lambda f: distance(get_box_center(f), previous_center),
                default=None
            )

            if closest_face is not None:
                new_center = get_box_center(closest_face)
                dist = distance(new_center, previous_center)
                face_width = closest_face[2]

                small_move_threshold = face_width * 0.4      # плавное движение
                hard_cut_threshold = face_width * 1.0        # резкое движение → прыжок

                if dist < small_move_threshold:
                    # Малое движение → плавно
                    tracked_face_box = closest_face
                    current_face_box = tracked_face_box
                    is_hard_cut = False

                elif dist < hard_cut_threshold:
                    # Среднее движение → тоже плавно
                    tracked_face_box = closest_face
                    current_face_box = tracked_face_box
                    is_hard_cut = False

                else:
                    # Резкое перемещение → моментальный прыжок
                    tracked_face_box = closest_face
                    current_face_box = tracked_face_box
                    is_hard_cut = True

            else:
                tracked_face_box = None

        # Потеря трека → берем самое большое лицо как новое (хардкат)
        if tracked_face_box is None and len(faces) > 0:
            is_hard_cut = True
            tracked_face_box = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)[0]
            current_face_box = tracked_face_box

        face_boxes.append((current_face_box, is_hard_cut))

    # -------------------------------
    # Дальше всё как в твоём оригинальном коде
    # -------------------------------

    # Fill missing boxes
    last_known_box = None
    interp_face_boxes = []
    for box, is_hard_cut in face_boxes:
        if box is not None:
            interp_face_boxes.append((box, is_hard_cut))
            last_known_box = box
        else:
            interp_face_boxes.append((last_known_box, False))
    
    if not any(b[0] is not None for b in interp_face_boxes):
        print("No faces found in the clip. Falling back to center crop.")
        return main_clip_resized.fx(vfx.crop, x_center=main_clip_resized.w / 2, width=target_width)
    
    first_valid_box_index = -1
    for i, (box, _) in enumerate(interp_face_boxes):
        if box is not None:
            first_valid_box_index = i
            break
    
    if first_valid_box_index != -1:
        first_box = interp_face_boxes[first_valid_box_index]
        for i in range(first_valid_box_index):
            interp_face_boxes[i] = (first_box[0], False)

    # Smooth crop path
    crop_path_x = []
    crop_half_width = target_width / 2
    crop_x_center = main_clip_resized.w / 2
    target_crop_x_center = main_clip_resized.w / 2
    smoothing_factor = 0.2

    for box, is_hard_cut in interp_face_boxes:
        if box is None:
            crop_path_x.append(crop_x_center)
            continue

        face_center_x, _ = get_box_center(box)

        if is_hard_cut:
            target_crop_x_center = face_center_x
            crop_x_center = face_center_x
        else:
            face_width = box[2]
            visible_left = crop_x_center - crop_half_width
            visible_right = crop_x_center + crop_half_width
            buffer = face_width * 0.5

            if not (visible_left + buffer < face_center_x < visible_right - buffer):
                target_crop_x_center = face_center_x
            
            crop_x_center = smoothing_factor * target_crop_x_center + (1 - smoothing_factor) * crop_x_center
        
        crop_path_x.append(crop_x_center)

    min_x = crop_half_width
    max_x = main_clip_resized.w - crop_half_width
    final_smoothed_x = np.clip(crop_path_x, min_x, max_x)

    def get_crop_x(t):
        return np.interp(t, timestamps, final_smoothed_x)

    def time_varying_crop(gf, t):
        frame = gf(t)
        center_x = get_crop_x(t)
        x1 = int(round(center_x - target_width / 2))
        x2 = x1 + target_width
        h, w, c = frame.shape
        x1 = max(0, x1)
        x2 = min(w, x2)
        x1 = x2 - target_width
        return frame[:, x1:x2]

    final_video = main_clip_resized.fl(time_varying_crop)
    final_video.audio = main_clip_raw.audio
    return final_video
