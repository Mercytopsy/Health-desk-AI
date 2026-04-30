import "dotenv/config";
import express from "express";
import cors from "cors";
import {
  AccessToken,
  AgentDispatchClient,
} from "livekit-server-sdk";

const app = express();

app.use(cors());
app.use(express.json());

const LIVEKIT_URL = process.env.LIVEKIT_URL;
const LIVEKIT_API_KEY = process.env.LIVEKIT_API_KEY;
const LIVEKIT_API_SECRET = process.env.LIVEKIT_API_SECRET;
const AGENT_NAME = process.env.LIVEKIT_AGENT_NAME || "HealthDesk-AI";

const dispatchClient = new AgentDispatchClient(
  LIVEKIT_URL,
  LIVEKIT_API_KEY,
  LIVEKIT_API_SECRET
);

app.get("/api/token", async (req, res) => {
  try {
    const identity = req.query.identity || `patient-${Date.now()}`;
    const roomName = req.query.room || "healthdesk-room";

    // 1. Dispatch the agent into the room.
    // If it already exists, we ignore the duplicate error.
    try {
      await dispatchClient.createDispatch(roomName, AGENT_NAME, {
        metadata: JSON.stringify({
          requestedBy: identity,
          source: "token-server",
        }),
      });

      console.log(`Dispatched ${AGENT_NAME} to ${roomName}`);
    } catch (err) {
      console.warn("Dispatch skipped or already exists:", err.message);
    }

    // 2. Create frontend participant token.
    const token = new AccessToken(
      LIVEKIT_API_KEY,
      LIVEKIT_API_SECRET,
      {
        identity,
        name: identity,
      }
    );

    token.addGrant({
      room: roomName,
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
      canPublishData: true,
    });

    const jwt = await token.toJwt();

    res.json({
      token: jwt,
      room: roomName,
      identity,
    });
  } catch (err) {
    console.error("Token error:", err);
    res.status(500).json({
      error: "Failed to create token",
      details: err.message,
    });
  }
});

app.listen(process.env.PORT || 3001, () => {
  console.log(`Token server running on http://localhost:${process.env.PORT || 3001}`);
});