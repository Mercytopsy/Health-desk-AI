import { useEffect, useMemo, useState } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  VideoTrack,
  useTracks,
} from "@livekit/components-react";
import "@livekit/components-styles";
import { Room, Track } from "livekit-client";
import "./App.css";

type Appointment = {
  id?: number;
  name?: string;
  date: string;
  time: string;
  intent?: string;
};

type Patient = {
  name: string;
  phone: string;
  returning: boolean;
};

type ToolEvent = {
  id: string;
  label: string;
  status: "loading" | "success" | "error";
  timestamp: string;
};

function App() {
  const [token, setToken] = useState<string>("");
  const room = useMemo(() => new Room(), []);

  useEffect(() => {
    async function getToken() {
      const identity = `patient-${crypto.randomUUID()}`;

      const res = await fetch(
        `${import.meta.env.VITE_TOKEN_ENDPOINT}?identity=${identity}`
      );

      const data = await res.json();
      setToken(data.token);
    }

    getToken();
  }, []);

  if (!token) {
    return <div className="loading">Preparing HealthDesk...</div>;
  }

  return (
    <LiveKitRoom
      room={room}
      token={token}
      serverUrl={import.meta.env.VITE_LIVEKIT_URL}
      connect
      audio
      video={false}
    >
      <HealthDeskUI room={room} />
      <RoomAudioRenderer />
    </LiveKitRoom>
  );
}

function HealthDeskUI({ room }: { room: Room }) {
  const [patient, setPatient] = useState<Patient | null>(null);
  const [slots, setSlots] = useState<string[]>([]);
  const [slotDate, setSlotDate] = useState<string>("");
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([]);
  const [sessionEnded, setSessionEnded] = useState(false);

  function addToolEvent(label: string, status: ToolEvent["status"] = "success") {
    setToolEvents((prev) => [
      {
        id: crypto.randomUUID(),
        label,
        status,
        timestamp: new Date().toLocaleString(),
      },
      ...prev,
    ]);
  }

  useEffect(() => {
    room.registerRpcMethod("patientIdentified", async (data) => {
      const payload = JSON.parse(data.payload);
      setPatient(payload);
      addToolEvent(
        payload.returning
          ? `Returning patient found: ${payload.name}`
          : `New patient registered: ${payload.name}`
      );
      return "patientIdentified received";
    });

    room.registerRpcMethod("slotsLoaded", async (data) => {
      const payload = JSON.parse(data.payload);
      setSlotDate(payload.date);
      setSlots(payload.slots);
      addToolEvent("Fetching slots complete ✅");
      return "slotsLoaded received";
    });

    room.registerRpcMethod("appointmentBooked", async (data) => {
      const payload = JSON.parse(data.payload);
      setAppointments((prev) => [...prev, payload]);
      addToolEvent("Booking confirmed ✅");
      return "appointmentBooked received";
    });

    room.registerRpcMethod("appointmentsLoaded", async (data) => {
      const payload = JSON.parse(data.payload);
      setAppointments(payload.appointments);
      addToolEvent("Appointments loaded ✅");
      return "appointmentsLoaded received";
    });

    room.registerRpcMethod("appointmentCancelled", async (data) => {
      const payload = JSON.parse(data.payload);
      setAppointments((prev) =>
        prev.filter(
          (a) => !(a.date === payload.date && a.time === payload.time)
        )
      );
      addToolEvent("Appointment cancelled ✅");
      return "appointmentCancelled received";
    });

    room.registerRpcMethod("appointmentRescheduled", async (data) => {
      const payload = JSON.parse(data.payload);

      setAppointments((prev) =>
        prev.map((a) =>
          a.date === payload.old_date && a.time === payload.old_time
            ? { ...a, date: payload.new_date, time: payload.new_time }
            : a
        )
      );

      addToolEvent("Appointment rescheduled ✅");
      return "appointmentRescheduled received";
    });

    room.registerRpcMethod("sessionEnded", async () => {
      setSessionEnded(true);
      addToolEvent("Call ended. Summary generated ✅");
      return "sessionEnded received";
    });

    return () => {
      room.unregisterRpcMethod("patientIdentified");
      room.unregisterRpcMethod("slotsLoaded");
      room.unregisterRpcMethod("appointmentBooked");
      room.unregisterRpcMethod("appointmentsLoaded");
      room.unregisterRpcMethod("appointmentCancelled");
      room.unregisterRpcMethod("appointmentRescheduled");
      room.unregisterRpcMethod("sessionEnded");
    };
  }, [room]);

  return (
    <main className="page">
      <section className="hero">
        <div>
          <p className="eyebrow">HealthDesk AI</p>
          <h1>Dr. Aria</h1>
          <p>Your AI health concierge</p>
        </div>

        <AgentVideo />
      </section>

      <section className="grid">
        <div className="card">
          <h2>Patient</h2>
          {patient ? (
            <>
              <p><strong>Name:</strong> {patient.name}</p>
              <p><strong>Phone:</strong> {patient.phone}</p>
              <p><strong>Status:</strong> {patient.returning ? "Returning" : "New"}</p>
            </>
          ) : (
            <p>Waiting for patient identification...</p>
          )}
        </div>

        <div className="card">
          <h2>Tool Activity</h2>
          {toolEvents.length === 0 ? (
            <p>No tools called yet.</p>
          ) : (
            <ul className="events">
              {toolEvents.map((event) => (
                <li key={event.id} className={event.status}>
                  <span>{event.label}</span>
                  <small>{event.timestamp}</small>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card">
          <h2>Available Slots</h2>
          {slots.length > 0 ? (
            <>
              <p>{slotDate}</p>
              <div className="slots">
                {slots.map((slot) => (
                  <span key={slot}>{slot}</span>
                ))}
              </div>
            </>
          ) : (
            <p>No slots loaded yet.</p>
          )}
        </div>

        <div className="card">
          <h2>Appointments</h2>
          {appointments.length > 0 ? (
            <ul className="appointments">
              {appointments.map((appt, index) => (
                <li key={`${appt.date}-${appt.time}-${index}`}>
                  <strong>{appt.date}</strong> at <strong>{appt.time}</strong>
                  <br />
                  <span>{appt.intent || "consultation"}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p>No appointments yet.</p>
          )}
        </div>
      </section>

      {sessionEnded && (
        <section className="summary">
          <h2>Call Summary</h2>
          <p>
            Conversation completed with{" "}
            <strong>{patient?.name || "the patient"}</strong>.
          </p>

          <h3>Appointments</h3>
          {appointments.length > 0 ? (
            <ul>
              {appointments.map((appt, index) => (
                <li key={index}>
                  {appt.date} at {appt.time} — {appt.intent || "consultation"}
                </li>
              ))}
            </ul>
          ) : (
            <p>No confirmed appointments captured.</p>
          )}

          <h3>User Preferences</h3>
          <p>No explicit preferences captured yet.</p>

          <h3>Timestamp</h3>
          <p>{new Date().toLocaleString()}</p>
        </section>
      )}
    </main>
  );
}

function AgentVideo() {
  const tracks = useTracks([Track.Source.Camera], {
    onlySubscribed: true,
  });

  const videoTrack = tracks[0];

  if (!videoTrack) {
    return <div className="avatar-placeholder">Waiting for Dr. Aria video...</div>;
  }

  return (
    <div className="avatar">
      <VideoTrack trackRef={videoTrack} />
    </div>
  );
}

export default App;