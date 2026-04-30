## HealthDesk AI

🌐 **Live App:** https://health-desk-ai-rho.vercel.app

HealthDesk AI is an intelligent voice-based assistant designed to handle patient interactions in real time.

---

### 🚀 Features

HealthtDesk AI supports multiple patient intents, including:

#### 📅 Appointment Management

- **Book Appointments**  
  Schedule new appointments through a voice-driven interaction.

- **View Appointments (`get_appointments`)**  
  Retrieve existing bookings for a patient.

- **Cancel Appointments (`cancel_appointment`)**  
  Cancel a scheduled appointment quickly and efficiently.

- **Reschedule Appointments**  
  Modify an existing appointment to a new date or time.


---

## 🎥 HealthDesk AI Demo

<p align="center">
  <a href="https://youtu.be/0GaWVmGoIW8">
    <img src="https://img.youtube.com/vi/0GaWVmGoIW8/0.jpg" alt="HealthDesk AI Demo" width="700"/>
  </a>
</p>

<p align="center">
  <em>HealthDesk AI – Voice-Based Appointment Booking Demo</em>
</p>

---

#### Cost Per Call Breakdown

Estimated cost depends on call length and provider usage.

For a typical 3-minute call:

| Service | Usage | Estimated Cost |
|---|---:|---:|
| LiveKit Cloud | Room/media transport | Usage-based |
| Deepgram STT | ~3 minutes audio | ~$0.0175 |
| OpenAI GPT-4o-mini | ~2k input + 500 output tokens | <$0.01 |
| Cartesia TTS | ~1–2 minutes generated speech | Usage-based |
| Tavus Avatar | Conversational avatar minutes | Usage-based |

---
#### Smart Edge-Case Handling

HealthDesk AI handles common real-world appointment issues:

| Edge Case | Handling |
|---|---|
| Missing phone number | Assistant asks for phone number before continuing |
| Missing date or time | Assistant asks one clear follow-up question |
| No available slots | Assistant offers the user another date |
| Cancel appointment not found | Assistant explains no active appointment was found |
| Reschedule conflict | Assistant tells user the new slot is already taken |
