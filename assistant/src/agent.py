
import json
import logging
import os
import sqlite3
from typing import Annotated

from dotenv import load_dotenv
from pathlib import Path
from datetime import date
from pydantic import Field

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomOutputOptions,
    RunContext,
    cli,
    function_tool,
    inference,
    llm,
    metrics,
    room_io,
)
from livekit.plugins import noise_cancellation, silero, tavus


logger = logging.getLogger("healthdesk-agent")

# load_dotenv()
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


DB_PATH = "healthdesk.db"

TAVUS_REPLICA_ID = os.environ.get("TAVUS_REPLICA_ID", "")
TAVUS_PERSONA_ID = os.environ.get("TAVUS_PERSONA_ID", "")




def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
              CREATE TABLE IF NOT EXISTS patients (
               phone     TEXT PRIMARY KEY,
               name      TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP
              )        
      """)
    
    c.execute("""

            CREATE TABLE IF NOT EXISTS appointments (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              phone         TEXT NOT NULL,
              name          TEXT,
              date          TEXT NOT NULL,
              time          TEXT NOT NULL,
              intent        TEXT DEFAULT 'booking appointment',
              status     TEXT DEFAULT 'confirmed',
              created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(date, time)
              )
            """)
    conn.commit()
    conn.close()



def get_remote_participant_identity(ctx: JobContext) -> str:
    """
    Get the patient's browser participant identity.
    Excludes Tavus avatar and agent-like participants.
    """
    for participant in ctx.room.remote_participants.values():
        identity = participant.identity.lower()

        if identity.startswith("tavus-avatar-agent"):
            continue

        if identity.startswith("healthdesk-ai"):
            continue

        return participant.identity

    raise llm.LLMToolException("No patient/browser participant found")


async def rpc_to_frontend(ctx: JobContext, method: str, payload: dict) -> str:
    """Send an RPC call to the patient's browser to update the UI."""
    local = ctx.room.local_participant
    if not local:
        raise llm.LLMToolException("Agent not connected to room")

    destination = get_remote_participant_identity(ctx)

    try:
        response = await local.perform_rpc(
            destination_identity=destination,
            method=method,
            payload=json.dumps(payload),
            response_timeout=10.0,
        )
        return response

    except Exception as e:
        logger.warning(f"RPC to frontend failed: {method} → {destination}: {e}")
        return "RPC failed, but tool completed"



async def notify_tool(
    ctx: JobContext,
    label: str,
    status: str = "loading",
    details: dict | None = None,
):
    """
    Notify the frontend whenever a tool starts, succeeds, or fails.
    Status can be: loading, success, error
    """
    await rpc_to_frontend(ctx, "toolStatus", {
        "label": label,
        "status": status,
        "details": details or {},
    })






class HealthDeskAgent(Agent):
    """
    AI front-desk voice agent. Responsible for Handling patient identification and appointment booking via tool calls.
    """

    def __init__(self, ctx: JobContext) -> None:
        self._ctx = ctx
        today = date.today().isoformat()
        super().__init__(
        instructions=f"""
            You are Grace, a warm clinic front-desk voice assistant.

            Today's date is {today}. Use this date when interpreting words like today, tomorrow, Friday, next week, or next month.

            When using appointment tools, always pass dates in YYYY-MM-DD format.
            Never invent past dates.

            Your job is to help patients identify themselves and manage appointments:
            book, view, cancel, or reschedule.

            Speak naturally and briefly.
            Use 1 to 2 short sentences per reply.
            Do not use markdown, bullets, symbols, or stage directions.
            Confirm important details before taking action.

            Required flow:
            1. Greet the patient.
            2. Ask for name and phone number.
            3. Call identify_patient.
            4. Ask what they need help with.
            5. Use the correct appointment tool.
            6. Confirm the outcome.
            7. Ask if they need anything else.
            8. Call end_conversation when they are done.

            Tool rules:
            - Always call identify_patient before appointment actions.
            - For booking, always call fetch_available_slots before book_appointment.
            - Confirm date and time before booking.
            - Confirm appointment details before cancelling or rescheduling.
            - If information is missing, ask one clear question.
            - Do not pretend to use tools. Call the actual tool.

            Intent mapping:
            - New or returning patient: identify_patient
            - Book appointment: fetch_available_slots, then book_appointment
            - View appointments: get_appointments
            - Cancel appointment: cancel_appointment
            - Reschedule appointment: reschedule_appointment
            - Goodbye or finished: end_conversation

            Opening line:
            Hello! Welcome to HealthDesk. I'm Grace, your AI health concierge. Could I start with your name and phone number, please?
            """,
        )
  



    @function_tool
    async def identify_patient(
        self,
        context: RunContext,
        phone: Annotated[str, Field(description="Patient's phone number (digits only)")],
        name: Annotated[str, Field(description="Patient's name if provided")] = "",
    ) -> str:
        """Look up or register a patient by phone number. Always call this first."""
        #await notify_tool(self._ctx, "Identifying patient...", "loading")

        phone_clean = "".join(filter(str.isdigit, phone))
        if len(phone_clean) < 10:
            return "Phone number seems too short — please ask the patient to repeat it."

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT phone, name FROM patients WHERE phone=?", (phone_clean,))
        row = c.fetchone()

        if row:
            conn.close()
            await rpc_to_frontend(self._ctx, "patientIdentified", {
                "phone": row[0], "name": row[1], "returning": True
            })
            return f"Returning patient found: {row[1]}. Phone ends in {phone_clean[-4:]}."
        else:
            resolved_name = name.strip() or "Guest"
            c.execute(
                "INSERT OR IGNORE INTO patients (phone, name) VALUES (?, ?)",
                (phone_clean, resolved_name),
            )
            conn.commit()
            conn.close()

            #await notify_tool(self._ctx, f"Patient identified: {name} ✅", "success")
            await rpc_to_frontend(self._ctx, "patientIdentified", {
                "phone": phone_clean, "name": resolved_name, "returning": False
            })
            
            return f"New patient registered: {resolved_name}. Phone ends in {phone_clean[-4:]}."



    @function_tool
    async def fetch_available_slots(
        self,
        context: RunContext,
        date: Annotated[str, Field(description="Date in YYYY-MM-DD format")],
    ) -> str:
        """Get available appointment slots for a given date. Call this before booking."""

        await notify_tool(self._ctx, "Fetching Slots...", "loading")
        
        all_slots = [
            "09:00 AM", "09:30 AM", "10:00 AM", "10:30 AM",
            "11:00 AM", "11:30 AM", "02:00 PM", "02:30 PM",
            "03:00 PM", "03:30 PM", "04:00 PM", "04:30 PM",
        ]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT time FROM appointments WHERE date=? AND status='confirmed'",
            (date,),
        )
        booked = {row[0] for row in c.fetchall()}
        conn.close()

        available = [s for s in all_slots if s not in booked]
        if not available:
            return f"No slots available on {date}. Ask the patient if they'd like a different date."

        await rpc_to_frontend(self._ctx, "slotsLoaded", {
            "date": date, "slots": available
        })
        return f"Available on {date}: {', '.join(available)}."

   

    @function_tool
    async def book_appointment(
        self,
        context: RunContext,
        phone: Annotated[str, Field(description="Patient's phone number")],
        name: Annotated[str, Field(description="Patient's name")],
        date: Annotated[str, Field(description="Date in YYYY-MM-DD format")],
        time: Annotated[str, Field(description="Time slot e.g. 10:00 AM")],
        intent: Annotated[str, Field(description="Reason for visit")] = "Book appointment",
    ) -> str:
        """Book an appointment. Only call after confirming date, time, and reason with the patient."""
        await notify_tool(self._ctx, "Booking appointment...", "loading")
        phone_clean = "".join(filter(str.isdigit, phone))
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute(
                """INSERT INTO appointments (phone, name, date, time, intent, status)
                   VALUES (?, ?, ?, ?, ?, 'confirmed')""",
                (phone_clean, name, date, time, intent),
            )
            conn.commit()
            appt_id = c.lastrowid

            await notify_tool(self._ctx, "Booking confirmed ✅", "success")
            await rpc_to_frontend(self._ctx, "appointmentBooked", {
                "id": appt_id, "name": name, "date": date,
                "time": time, "intent": intent,
            })
            return f"Appointment confirmed for {name} on {date} at {time} for {intent}."
        except sqlite3.IntegrityError:
            return f"{time} on {date} is already booked. Please offer the patient another slot."
        finally:
            conn.close()

    

    @function_tool
    async def get_appointments(
        self,
        context: RunContext,
        phone: Annotated[str, Field(description="Patient's phone number")],
    ) -> str:
        """Retrieve upcoming confirmed appointments for a patient."""
        phone_clean = "".join(filter(str.isdigit, phone))
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            """SELECT id, date, time, intent FROM appointments
               WHERE phone=? AND status='confirmed' ORDER BY date, time""",
            (phone_clean,),
        )
        rows = c.fetchall()
        conn.close()

        if not rows:
            return "No upcoming appointments found for this patient."

        await rpc_to_frontend(self._ctx, "appointmentsLoaded", {
            "appointments": [
                {"id": r[0], "date": r[1], "time": r[2], "intent": r[3]}
                for r in rows
            ]
        })
        summary = "; ".join([f"{r[1]} at {r[2]} ({r[3]})" for r in rows])
        return f"Found {len(rows)} appointment(s): {summary}."



    @function_tool
    async def cancel_appointment(
        self,
        context: RunContext,
        phone: Annotated[str, Field(description="Patient's phone number")],
        date: Annotated[str, Field(description="Date of appointment YYYY-MM-DD")],
        time: Annotated[str, Field(description="Time of appointment to cancel")],
    ) -> str:
        """Cancel a specific appointment after the patient confirms."""
        phone_clean = "".join(filter(str.isdigit, phone))
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            """UPDATE appointments SET status='cancelled'
               WHERE phone=? AND date=? AND time=? AND status='confirmed'""",
            (phone_clean, date, time),
        )
        affected = c.rowcount
        conn.commit()
        conn.close()

        if affected:
            await notify_tool(self._ctx, "Appointment Cancelled ✅", "success")
            await rpc_to_frontend(self._ctx, "appointmentCancelled", {
                "date": date, "time": time
            })
            return f"Appointment on {date} at {time} has been cancelled."
        
        # await notify_tool(self._ctx, "No active appointment found ❌", "error")
        return f"No active appointment found on {date} at {time}."



    @function_tool
    async def reschedule_appointment(
        self,
        context: RunContext,
        phone: Annotated[str, Field(description="Patient's phone number")],
        old_date: Annotated[str, Field(description="Original date YYYY-MM-DD")],
        old_time: Annotated[str, Field(description="Original time slot")],
        new_date: Annotated[str, Field(description="New date YYYY-MM-DD")],
        new_time: Annotated[str, Field(description="New time slot")],
    ) -> str:
        """Move an existing appointment to a new date and time."""
        phone_clean = "".join(filter(str.isdigit, phone))
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute(
                """UPDATE appointments SET date=?, time=?
                   WHERE phone=? AND date=? AND time=? AND status='confirmed'""",
                (new_date, new_time, phone_clean, old_date, old_time),
            )
            if c.rowcount == 0:
                conn.close()
                return f"No active appointment found on {old_date} at {old_time}."
            conn.commit()
            await notify_tool(self._ctx, "Appointment Rescheduled ✅", "success")
            await rpc_to_frontend(self._ctx, "appointmentRescheduled", {
                "old_date": old_date, "old_time": old_time,
                "new_date": new_date, "new_time": new_time,
            })
            return f"Appointment moved from {old_date} {old_time} to {new_date} {new_time}."
        except sqlite3.IntegrityError:
            conn.close()
            return f"{new_time} on {new_date} is already taken. Please offer another slot."
        finally:
            conn.close()

  

    @function_tool
    async def end_conversation(self, context: RunContext) -> str:
        """End the session gracefully. Call when the patient says goodbye or is finished."""
        await rpc_to_frontend(self._ctx, "sessionEnded", {})
        context.session.say(
            "Thank you for calling HealthDesk. Have a healthy day. Goodbye!"
        )
        return "Session ended."




server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="HealthDesk-AI")
async def healthdesk_agent(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    init_db()


    await ctx.connect()

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        llm=inference.LLM(model="openai/gpt-4o-mini"),
        tts=inference.TTS(
            model="cartesia/sonic-2",
            voice="db6b0ed5-d5d3-463d-ae85-518a07d3c2b4",
        ),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=False,
    )
  
    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        logger.info(f"Usage summary: {usage_collector.get_summary()}")

    ctx.add_shutdown_callback(log_usage)

  
    avatar = tavus.AvatarSession(
        replica_id=TAVUS_REPLICA_ID,   
        persona_id=TAVUS_PERSONA_ID,   
        avatar_participant_name="Grace",
    )
    await avatar.start(session, room=ctx.room)



    await session.start(
        agent=HealthDeskAgent(ctx),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
        room_output_options=RoomOutputOptions(
            audio_enabled=False,
            transcription_enabled=False,
        ),
    )


    session.generate_reply(
        instructions="Use your defined opening line and ask for the patient's name and phone number."
    )


if __name__ == "__main__":
    cli.run_app(server)
