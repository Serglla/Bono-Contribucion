from kokoro import KModel, KVoice
import soundfile as sf
import torch

# Configuración de dispositivo
device = 'cuda' if torch.cuda.is_available() else 'cpu'
print(f"Usando dispositivo: {device}")

# Cargar modelo y voces
# Nota: La primera vez que lo corras, va a descargar los modelos (unos cientos de MB)
model = KModel()
voices = {
    "em_alex": KVoice.from_path("en_us_male_alex"), # Ejemplo, ajustaremos si falla
    "em_santa": KVoice.from_path("es_es_male_santa"), 
    "ef_dora": KVoice.from_path("es_es_female_dora")
}

# Si las rutas de arriba fallan porque la librería es distinta, 
# usaremos una forma genérica que detecte las voces instaladas.
text = "Hola Sergio, soy Bruno. Estoy probando esta nueva voz para ver si te gusta más que la anterior."

# Vamos a intentar generar los tres archivos
for nombre, voz in voices.items():
    try:
        print(f"Generando audio para {nombre}...")
        # Ajustamos el texto según la voz si fuera necesario, pero probamos con el mismo
        samples, _ = model.generate(text, voz=voz, speed=1.12, device=device)
        sf.write(f"{nombre}.wav", samples, model.sample_rate)
        print(f"Archivo {nombre}.wav creado.")
    except Exception as e:
        print(f"No pude generar {nombre}: {e}")

print("Prueba terminada. Buscá los archivos .wav en la carpeta.")