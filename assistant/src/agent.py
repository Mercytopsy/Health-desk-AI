
import json
import logging
import os
import sqlite3
from typing import Annotated

from dotenv import load_dotenv
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
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("healthdesk-agent")

load_dotenv()

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

# def init_db():
#     conn = sqlite3.connect(DB_PATH)
#     c = conn.cursor()
#     c.execute("""
#         CREATE TABLE IF NOT EXISTS patients (
#             phone      TEXT PRIMARY KEY,
#             name       TEXT,
#             created_at TEXT DEFAULT CURRENT_TIMESTAMP
#         )
#     """)
#     c.execute("""
#         CREATE TABLE IF NOT EXISTS appointments (
#             id         INTEGER PRIMARY KEY AUTOINCREMENT,
#             phone      TEXT NOT NULL,
#             name       TEXT,
#             date       TEXT NOT NULL,
#             time       TEXT NOT NULL,
#             intent     TEXT DEFAULT 'consultation',
#             status     TEXT DEFAULT 'confirmed',
#             created_at TEXT DEFAULT CURRENT_TIMESTAMP,
#             UNIQUE(date, time)
#         )
#     """)
#     conn.commit()
#     conn.close()


# ─────────────────────────────────────────────
# RPC HELPERS
# ─────────────────────────────────────────────



# def get_remote_participant_identity(ctx: JobContext) -> str:
#     """
#     Get the patient's participant identity.
#     Excludes Tavus avatar participants (they join as "Tavus-avatar-agent").
#     """
#     for participant in ctx.room.remote_participants.values():
#         if not participant.identity.startswith("Tavus-avatar-agent"):
#             return participant.identity
#     raise llm.LLMToolException("No remote participant found")

# async def rpc_to_frontend(ctx: JobContext, method: str, payload: dict) -> str:
#     """Send an RPC call to the patient's browser to update the UI."""
#     local = ctx.room.local_participant
#     if not local:
#         raise llm.LLMToolException("Agent not connected to room")

#     destination = get_remote_participant_identity(ctx)
#     response = await local.perform_rpc(
#         destination_identity=destination,
#         method=method,
#         payload=json.dumps(payload),
#         response_timeout=5.0,
#     )
#     return response

# async def notify_tool(
#     ctx: JobContext, 
#     label: str, 
#     status: str = "loading", 
#     details:dict | None = None
# ):
    
#     """
#     This is to notify the frontend whenever a tool starts, succeeds, or fails.
#     Status can be: Fetching slots, Booking confirmed
#     """
#     await rpc_to_frontend(ctx, "toolStatus", {
#         "label": label,
#         "status": status,
#         "details": details or {},
#     })

def get_remote_participant_identity(ctx: JobContext) -> str:
    """
    Get the patient's participant identity.
    Excludes Tavus avatar participants (they join as "Tavus-avatar-agent").
    """
    for participant in ctx.room.remote_participants.values():
        if not participant.identity.startswith("Tavus-avatar-agent"):
            return participant.identity
    raise llm.LLMToolException("No remote participant found")


async def rpc_to_frontend(ctx: JobContext, method:str, payload: dict)-> str:
     """Send an RPC call to the patient's browser to update the UI."""
     local = ctx.room.local_participant
     if not local:
         raise llm.LLMToolException("Agent not connected to room")
     
     destination = get_remote_participant_identity(ctx)
     response = await local.perform_rpc(
         destination_identity=destination,
         method = method,
         payload = json.dumps(payload),
         response_timeout=5.0,
     )
     return response


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
        super().__init__(
            instructions="""You are Dr. Grace, a warm and professional AI front-desk assistant for a clinic.
           You speak with patients over voice and help them manage appointments.

            Your responsibilities:
            - Identify patients
            - Book appointments
            - View appointments
            - Cancel appointments
            - Reschedule appointments

            ────────────────────
            CONVERSATION STYLE
            ────────────────────
            - Speak in short, natural sentences — this is voice, not text.
            - Be warm, calm, and clear.
            - Never use markdown, bullet points, asterisks, or stage directions.
            - Confirm important details (name, phone number, date, time) before acting.
            - Keep responses concise — under 3 sentences per turn.

            ────────────────────
            INTENT DETECTION (VERY IMPORTANT)
            ────────────────────
            Before taking any action, determine the patient's intent.

            Supported intents:
            
            1. Identify patient → use identify_patient
            2. Book appointment → use fetch_available_slots then book_appointment
            3. View appointments → use get_appointments
            4. Cancel appointment → use cancel_appointment
            5. Reschedule appointment → use reschedule_appointment
            6. End conversation → use end_conversation

            Always choose the correct tool based on intent.
            ────────────────────
            STRICT TOOL RULES
            ────────────────────
            - ALWAYS call identify_patient before any booking action.
            - ALWAYS call fetch_available_slots before booking so you can offer real options.
            - ALWAYS confirm date and time with the patient before calling book_appointment.
            - For cancellations or modifications, confirm the existing appointment first.
            - Call end_conversation when the patient says goodbye or is done.

            ────────────────────
            CONVERSATION FLOW (in order)
            ────────────────────
            1. Greet the patient warmly.
            2. Ask for their name and phone number → call identify_patient.
            3. Ask what they need help with today.
            4. Handle their request using the appropriate tools.
            5. Confirm all booking details clearly before saving.
            6. Ask if there's anything else.
            7. End the call warmly → call end_conversation.

     
            Opening line:
            "Hello! Welcome to HealthDesk. I'm Dr. Aria, your AI health concierge. \
            Could I start with your name and phone number, please?" """,
        )



  
        

    @function_tool
    async def identify_patient(
        self,
        context: RunContext,
        phone: Annotated[str, Field(description="Patient's phone number (digits only)")],
        name: Annotated[str, Field(description="Patient's name if provided")] = "",
    ) -> str:
        """Look up or register a patient by phone number. Always call this first."""
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

        await notify_tool(self._ctx, "Fetching Slots", "loading")
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
            await rpc_to_frontend(self._ctx, "appointmentCancelled", {
                "date": date, "time": time
            })
            return f"Appointment on {date} at {time} has been cancelled."
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
        llm=inference.LLM(model="openai/gpt-4o"),
        tts = inference.TTS(model="cartesia/sonic-2",),
        # tts=inference.TTS(
        #     model="elevenlabs/eleven_turbo_v2_5",
        #     voice="cgSgspJ2msm6clMCkdW9",  # ElevenLabs Jessica — warm, professional
        # ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
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
        avatar_participant_name="Dr. Grace",
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
        ),
    )


    session.generate_reply(
        instructions="Use your defined opening line and ask for the patient's name and phone number."
    )


if __name__ == "__main__":
    cli.run_app(server)
