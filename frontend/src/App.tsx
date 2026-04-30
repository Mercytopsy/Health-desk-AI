import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import "./App.css";

function App() {
  const [status, setStatus] = useState("Preparing HealthDesk...");
  const [events, setEvents] = useState<string[]>([]);

  const [patientName, setPatientName] = useState("");
  const [appointments, setAppointments] = useState<any[]>([]);
  const [callEnded, setCallEnded] = useState(false);
  const [endedAt, setEndedAt] = useState("");

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    const room = new Room();
    let cleanedUp = false;

    const cleanup = () => {
      if (cleanedUp) return;
      cleanedUp = true;

      console.log("Cleaning up LiveKit room...");

      room.localParticipant.setMicrophoneEnabled(false).catch(console.warn);

      room.localParticipant.trackPublications.forEach((publication) => {
        publication.track?.stop();
      });

      room.disconnect();
    };

    window.addEventListener("beforeunload", cleanup);

    async function start() {
      try {
        setStatus("Getting LiveKit token...");

        const roomName = `healthdesk-room-${crypto.randomUUID()}`;

        const res = await fetch(
          `${import.meta.env.VITE_TOKEN_ENDPOINT}?identity=patient-test&room=${roomName}`
        );

        const data = await res.json();
        console.log("TOKEN DATA:", data);

        setStatus("Connecting to LiveKit...");

        await room.connect(import.meta.env.VITE_LIVEKIT_URL, data.token);

        setStatus("Connected to HealthDesk");

        await room.localParticipant.setMicrophoneEnabled(true);
        console.log("Microphone enabled");

        room.registerRpcMethod("toolStatus", async (data) => {
          const payload = JSON.parse(data.payload);
          setEvents((prev) => [`${payload.label}`, ...prev]);
          return "toolStatus received";
        });

        room.registerRpcMethod("patientIdentified", async (data) => {
          const payload = JSON.parse(data.payload);

          setPatientName(payload.name);

          setEvents((prev) => [`Patient identified ✅`, ...prev]);

          return "patientIdentified received";
        });

        room.registerRpcMethod("slotsLoaded", async () => {
          return "slotsLoaded received";
        });

        room.registerRpcMethod("appointmentBooked", async (data) => {
          const payload = JSON.parse(data.payload);

          setAppointments((prev) => [...prev, payload]);

          return "appointmentBooked received";
        });

        room.registerRpcMethod("appointmentCancelled", async () => {
          return "Appointment Cancelled";
        });

        room.registerRpcMethod("appointmentRescheduled", async () => {
          return "Appointment Rescheduled";
        });

        room.registerRpcMethod("sessionEnded", async () => {
          setCallEnded(true);
          setEndedAt(new Date().toLocaleString());

          setEvents((prev) => ["Call ended. Summary generated ✅", ...prev]);
          setStatus("Call ended");

          cleanup();

          return "sessionEnded received";
        });

        // room.registerRpcMethod("sessionEnded", async () => {
        //   setCallEnded(true);
        //   setEndedAt(new Date().toLocaleString());
        //   setEvents((prev) => ["Call ended. Summary generated ✅", ...prev]);
        //   setStatus("Call ended");

        //   return "sessionEnded received";
        // });

        room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
          console.log("Track subscribed:", track.kind, participant.identity);

          if (track.kind === Track.Kind.Video && videoRef.current) {
            track.attach(videoRef.current);
          }

          if (track.kind === Track.Kind.Audio && audioRef.current) {
            track.attach(audioRef.current);

            audioRef.current.play().catch((err) => {
              console.warn("Audio play blocked:", err);
            });
          }
        });
      } catch (err) {
        console.error("LiveKit connection error:", err);
        setStatus("Connection failed. Check console.");
      }
    }

    start();

    return () => {
      window.removeEventListener("beforeunload", cleanup);
      cleanup();
    };
  }, []);

  return (
    <main className="page">
      <audio ref={audioRef} autoPlay hidden />

      <section className="card">
        <h1>HealthDesk AI</h1>
        <p>{status}</p>
      </section>

      <section className="card">
        <h2>👤 Grace</h2>
        <video ref={videoRef} autoPlay playsInline />
      </section>

      <section className="card">
        <h2>Assistant Activity</h2>
        {events.length === 0 ? (
          <p>No activity yet</p>
        ) : (
          <ul>
            {events.map((event, index) => (
              <li key={index}>{event}</li>
            ))}
          </ul>
        )}
      </section>

      {callEnded && (
        <section className="summary">
          <h2>Call Summary</h2>

          <p>
            <strong>Summary:</strong>{" "}
            Conversation completed with {patientName || "the patient"}.
          </p>

          <h3>Appointments</h3>
          {appointments.length === 0 ? (
            <p>No appointments were booked.</p>
          ) : (
            <ul>
              {appointments.map((appt, index) => (
                <li key={index}>
                  {appt.date} at {appt.time} — {appt.intent || "consultation"}
                </li>
              ))}
            </ul>
          )}

          <h3>User Preferences</h3>
          <p>No explicit preferences captured.</p>

          <h3>Timestamp</h3>
          <p>{endedAt}</p>
        </section>
      )}
    </main>
  );
}

export default App;
