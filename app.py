import streamlit as st
import cv2
import time
from ultralytics import YOLO

# --- INTERFACE ET STYLE ---
st.set_page_config(page_title="Aqua-Cleaner AI", page_icon="🌊", layout="centered")

st.markdown("""
    <style>
    /* Thème Bleu et Blanc */
    .stApp {
        background-color: #001f3f; /* Fond bleu marine */
        color: #FFFFFF; /* Texte blanc */
    }
    .main-title {
        color: #FFFFFF;
        background-color: #005A9C;
        padding: 15px;
        border-radius: 10px;
        text-align: center;
        font-family: 'Arial Black', sans-serif;
    }
    .terminal-box {
        background-color: #003366; /* Bleu foncé */
        color: #FFFFFF; /* Blanc */
        font-family: 'Courier New', Courier, monospace;
        padding: 10px;
        border-radius: 5px;
        height: 300px;
        overflow-y: scroll;
        border: 1px solid #005A9C;
    }
    h1, h2, h3, p, div {
        color: #FFFFFF;
    }
    .stMetric-value { color: #00aaff !important; }
    </style>
""", unsafe_allow_html=True)

st.markdown("<h1 class='main-title'>🌊 Aqua-Cleaner 🌊</h1>", unsafe_allow_html=True)
st.subheader("Analyse de Déchets Aquatiques par IA")

# --- CONTRÔLES ---
video_file = st.file_uploader("Importer la vidéo du robot (MP4)", type=["mp4", "mov", "avi"])
start_processing = st.button("▶️ Démarrer l'analyse")

# --- LOGIQUE DE TRAITEMENT ---
if video_file is not None and start_processing:
    temp_video_path = "temp_uploaded_video.mp4"
    with open(temp_video_path, "wb") as f:
        f.write(video_file.read())

    # Load your custom OpenVINO model
    model = YOLO("bottle_weights_openvino_model", task="detect")

    cap = cv2.VideoCapture(temp_video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # --- SPEED OPTIMIZATION CONFIG ---
    FRAME_SKIP = 5  # Process every 5th frame (change to 3 or 4 if it skips too much)
    PROCESS_WIDTH = 320  # Resize width for AI speedup (maintains aspect ratio)
    scale_factor = PROCESS_WIDTH / orig_w
    process_h = int(orig_h * scale_factor)

    MIN_COLLECTION_AREA = (orig_w * orig_h) * 0.10
    FRAMES_TO_WAIT = int(fps * 3.0)

    collection_primed = False
    frames_since_massive_box = 0

    collected_bottles = 0
    collected_cartons = 0
    frame_idx = 0

    cached_crop = None
    collected_images = []

    st.write("### Journal de bord en direct")
    log_placeholder = st.empty()
    progress_bar = st.progress(0)

    log_text = "Initialisation du moteur Aqua-Cleaner (Mode Optimisé)...\n"
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            break

        frame_idx += 1
        massive_bottle_in_view = False

        # --- OPTIMIZATION 1: SKIP FRAMES ---
        if frame_idx % FRAME_SKIP != 0:
            # We still tick the timer forward for the collection confirmation
            if collection_primed:
                frames_since_massive_box += 1
                if frames_since_massive_box >= FRAMES_TO_WAIT:
                    collected_bottles += 1
                    collection_primed = False
                    if cached_crop is not None:
                        rgb_crop = cv2.cvtColor(cached_crop, cv2.COLOR_BGR2RGB)
                        collected_images.append(rgb_crop)
                    log_text += f"> Image {frame_idx}: 🍾 Bouteille collectée ! (Total: {collected_bottles})\n"
            continue

        # --- OPTIMIZATION 2: DOWNCOMPRESS RESO FOR AI ---
        small_frame = cv2.resize(frame, (PROCESS_WIDTH, process_h), interpolation=cv2.INTER_AREA)

        results = model.predict(small_frame, conf=0.5, verbose=False)[0]

        for box in results.boxes:
            # Scale coordinates back up to original size for high-quality cropping
            x1_s, y1_s, x2_s, y2_s = box.xyxy[0].tolist()
            x1, y1 = int(x1_s / scale_factor), int(y1_s / scale_factor)
            x2, y2 = int(x2_s / scale_factor), int(y2_s / scale_factor)

            box_area = (x2 - x1) * (y2 - y1)

            if box_area >= MIN_COLLECTION_AREA:
                massive_bottle_in_view = True

                y1_c, y2_c = max(0, y1), min(orig_h, y2)
                x1_c, x2_c = max(0, x1), min(orig_w, x2)
                if (x2_c - x1_c) > 0 and (y2_c - y1_c) > 0:
                    cached_crop = frame[y1_c:y2_c, x1_c:x2_c].copy()

        # Collection Logic
        if massive_bottle_in_view:
            collection_primed = True
            frames_since_massive_box = 0
        elif collection_primed:
            frames_since_massive_box += 1
            if frames_since_massive_box >= FRAMES_TO_WAIT:
                collected_bottles += 1
                collection_primed = False

                if cached_crop is not None:
                    rgb_crop = cv2.cvtColor(cached_crop, cv2.COLOR_BGR2RGB)
                    collected_images.append(rgb_crop)

                log_text += f"> Image {frame_idx}: 🍾 Bouteille collectée ! (Total: {collected_bottles})\n"

        if frame_idx % 20 == 0:
            current_log = log_text + f"Traitement rapide de l'image {frame_idx}/{total_frames}...\n"
            log_placeholder.markdown(f'<div class="terminal-box">{current_log.replace(chr(10), "<br>")}</div>',
                                     unsafe_allow_html=True)
            progress_bar.progress(min(frame_idx / total_frames, 1.0))

    cap.release()

    log_text += f"\n--- TRAITEMENT VIDÉO TERMINÉ ---\n"
    log_placeholder.markdown(f'<div class="terminal-box">{log_text.replace(chr(10), "<br>")}</div>',
                             unsafe_allow_html=True)
    progress_bar.progress(1.0)

    # --- RAPPORTS ET PHOTOS ---
    st.success("Analyse terminée !")

    st.markdown("### 📊 Statistiques de Collecte")
    col1, col2, col3 = st.columns(3)
    col1.metric("Bouteilles d'eau", collected_bottles)
    col2.metric("Cartons", collected_cartons)
    col3.metric("Total", collected_bottles + collected_cartons)

    if collected_images:
        st.markdown("### 📸 Déchets Collectés")
        cols = st.columns(len(collected_images))
        for i, (col, img) in enumerate(zip(cols, collected_images)):
            col.image(img, caption=f"Bouteille #{i + 1}")

    st.markdown("### 🔍 Prédiction de la Source")
    st.info(
        "**Source principale identifiée :** Activité humaine. \n\nLa présence de bouteilles en plastique à usage unique indique un rejet par les humains près de l'environnement aquatique.")

    st.markdown("### ♻️ Protocole d'Élimination Recommandé")
    st.warning("""
    Actions recommandées :
    * **Bouteilles d'eau (Plastique PET) :** Vider le liquide restant. Acheminer vers un centre de recyclage plastique (Type 1).
    * **Cartons :** Aucun carton détecté.
    """)