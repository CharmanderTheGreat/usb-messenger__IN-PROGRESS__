"""
face_auth.py
------------
Phase 2 (part A): Face-based biometric factor.

Responsibilities:
  1. Enroll a face: capture from webcam, compute a feature embedding,
     encrypt it, and produce data to store in face_template.dat on the USB.
  2. Verify a face: capture a live frame, compute its embedding, and
     compare it against the stored (decrypted) template.

Design notes:
  - We deliberately avoid the `face_recognition` (dlib) library here
    because dlib is a heavy, slow-to-build dependency that complicates
    packaging a portable .exe. Instead we use OpenCV's built-in
    LBPH (Local Binary Pattern Histogram) face recognizer, which:
      * ships as part of opencv-contrib-python (no separate compile step)
      * is fast enough for a single-user "is this the enrolled person"
        check (not large-scale face search, which is not our use case)
  - We NEVER store a raw photo. What gets saved is a trained LBPH model
    (a numeric representation), and even that is encrypted at rest using
    the same password-derived key approach as identity.py.
  - This module has zero knowledge of passwords/keys directly -- it just
    hands back raw bytes to encrypt/decrypt, using the same primitives
    as identity.py so the two modules compose cleanly.
"""

import os
import sys
import io
import cv2
import numpy as np

# ---- Tunable constants -----------------------------------------------


def _find_cascade_file() -> str:
    """
    Locate the Haar cascade XML file needed for face detection.
    Some newer opencv-contrib-python builds ship without the data files
    bundled in cv2.data.haarcascades, so we check (in order):
      1. The PyInstaller-bundled copy (when running as a packaged .exe)
      2. A local copy placed next to this script (development/testing)
      3. The installed opencv package's own data folder
    """
    # Case 1: running as a PyInstaller-frozen executable. PyInstaller
    # extracts bundled data files into a temp folder pointed to by
    # sys._MEIPASS -- this only exists when frozen.
    if hasattr(sys, "_MEIPASS"):
        frozen_path = os.path.join(
            sys._MEIPASS, "cv2", "data", "haarcascade_frontalface_default.xml"
        )
        if os.path.exists(frozen_path):
            return frozen_path

    # Case 2: local copy sitting next to this script (normal dev/run case)
    local_copy = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "haarcascade_frontalface_default.xml",
    )
    if os.path.exists(local_copy):
        return local_copy

    # Case 3: fall back to whatever the installed opencv package provides
    package_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    if os.path.exists(package_path):
        return package_path

    raise FileNotFoundError(
        "haarcascade_frontalface_default.xml not found. Download it from "
        "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/"
        "haarcascade_frontalface_default.xml and place it in the same folder as this script."
    )


FACE_CASCADE_PATH = _find_cascade_file()
ENROLLMENT_SAMPLE_COUNT = 20  # number of frames captured during enrollment
FACE_SIZE = (200, 200)  # normalized size for all face crops
LBPH_CONFIDENCE_THRESHOLD = 65.0  # LOWER LBPH distance = more confident match.
# Anything above this is treated as "not a match".
# Tune this during real-world testing.


class FaceAuthError(Exception):
    """Raised when enrollment or verification cannot proceed (no camera, no face found, etc.)."""

    pass


def _get_face_detector() -> cv2.CascadeClassifier:
    detector = cv2.CascadeClassifier(FACE_CASCADE_PATH)
    if detector.empty():
        raise FaceAuthError(
            "Could not load face detection model from OpenCV data path."
        )
    return detector


def _detect_largest_face(gray_frame: np.ndarray, detector: cv2.CascadeClassifier):
    """Return the largest detected face region as (x, y, w, h), or None."""
    faces = detector.detectMultiScale(
        gray_frame, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80)
    )
    if len(faces) == 0:
        return None
    # Pick the largest face in frame (in case of multiple people, assume the
    # closest/largest one is the intended user)
    return max(faces, key=lambda f: f[2] * f[3])


def _capture_frames_from_webcam(
    num_frames: int, camera_index: int = 0
) -> list[np.ndarray]:
    """
    Open the webcam and capture `num_frames` grayscale face crops.
    Raises FaceAuthError if the camera can't be opened or no face is ever found.
    """
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise FaceAuthError(
            "Could not access webcam. Check camera permissions/connection."
        )

    detector = _get_face_detector()
    collected = []

    try:
        attempts = 0
        max_attempts = num_frames * 10  # allow retries for frames with no face
        while len(collected) < num_frames and attempts < max_attempts:
            attempts += 1
            ret, frame = cap.read()
            if not ret:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            face_box = _detect_largest_face(gray, detector)
            if face_box is None:
                continue

            x, y, w, h = face_box
            face_crop = gray[y : y + h, x : x + w]
            face_crop = cv2.resize(face_crop, FACE_SIZE)
            collected.append(face_crop)
    finally:
        cap.release()

    if len(collected) == 0:
        raise FaceAuthError(
            "No face detected during capture. Check lighting/camera position."
        )

    return collected


def enroll_face(
    camera_index: int = 0, num_samples: int = ENROLLMENT_SAMPLE_COUNT
) -> bytes:
    """
    Capture multiple face samples from the webcam, train an LBPH model on
    them, and return the trained model serialized as bytes.

    This is what gets encrypted and written to face_template.dat.
    """
    face_samples = _capture_frames_from_webcam(num_samples, camera_index)

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    # LBPH needs integer labels per sample; since this is single-user
    # enrollment, every sample gets the same label (0 = "the enrolled user").
    labels = np.zeros(len(face_samples), dtype=np.int32)
    recognizer.train(face_samples, labels)

    # Serialize the trained model to an in-memory buffer, then to bytes.
    tmp_path = "_lbph_model_tmp.xml"
    recognizer.write(tmp_path)
    with open(tmp_path, "rb") as f:
        model_bytes = f.read()
    os.remove(tmp_path)

    return model_bytes


def verify_face(stored_model_bytes: bytes, camera_index: int = 0) -> bool:
    """
    Capture a live frame, compare it against the previously enrolled model.
    Returns True if it's a confident match, False otherwise.
    """
    live_samples = _capture_frames_from_webcam(num_frames=1, camera_index=camera_index)
    live_face = live_samples[0]

    tmp_path = "_lbph_model_verify_tmp.xml"
    with open(tmp_path, "wb") as f:
        f.write(stored_model_bytes)

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(tmp_path)
    os.remove(tmp_path)

    label, confidence = recognizer.predict(live_face)
    # LBPH: LOWER confidence value = better match (it's a distance metric,
    # not a similarity score -- this trips people up, so it's called out here).
    is_match = confidence <= LBPH_CONFIDENCE_THRESHOLD
    return is_match


# ---- Manual test / demo ------------------------------------------------
if __name__ == "__main__":
    print("=== Phase 2 Demo: Face Enrollment & Verification ===\n")
    print("NOTE: This requires an actual webcam to run. It will NOT work")
    print("in a headless/sandboxed environment without a camera device.\n")

    try:
        print("[1] Enrolling face -- look at the camera...")
        model = enroll_face()
        print(f"    Enrollment complete. Model size: {len(model)} bytes")

        print("\n[2] Verifying face -- look at the camera again...")
        result = verify_face(model)
        print(f"    Match result: {result}")

    except FaceAuthError as e:
        print(f"    [Expected in sandbox/no-camera environment]: {e}")
