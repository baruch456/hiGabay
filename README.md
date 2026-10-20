
# 📅 AI Calendar Agent

A conversational AI assistant powered by LangChain, FastAPI, and Groq, designed to manage your Google Calendar events through a simple chat interface built with Streamlit.

### [➡️ Try the Live Demo](https://calender-agent.onrender.com/)

---

## ✨ Key Features

-   **Natural Language Interaction**: Manage your calendar using plain English.
-   **Create Events**: "Book a meeting with the team tomorrow at 2 PM."
-   **List Events**: "What's on my schedule for this Friday?"
-   **Postpone Events**: "Reschedule my 10 AM meeting to 3 PM."
-   **Delete Events**: "Cancel the project sync."
-   **Smart Tool Selection**: Uses LangChain's LangGraph to intelligently decide which calendar tool to use based on your request.
-   **Web Interface**: Easy-to-use chat UI built with Streamlit.
-   **Read-only Calendar View**: Browse calendar events by month without editing them.
-   **Containerized**: Fully containerized with Docker for easy deployment.

## 🏛️ Architecture

The application uses a decoupled architecture where the Streamlit UI communicates with a FastAPI backend. The backend processes the user's request using a LangGraph agent, which leverages the Groq LLM to understand intent and call the appropriate Google Calendar tools.

```
+----------------+      +---------------------+      +-----------------+
|   User         |----->|   Streamlit UI      |----->|  FastAPI Backend|
+----------------+      +---------------------+      +-----------------+
                                                            |
                                                            v
+-----------------------------------------------------------+
|                      LangGraph Agent                      |
|  +-----------------+     +-----------------+              |
|  | LLM (Groq)      || Tool Dispatcher |              |
|  +-----------------+     +-----------------+              |
|                              |                            |
|                              v                            |
|                 +--------------------------+              |
|                 | Google Calendar API Tools|              |
|                 +--------------------------+              |
+-----------------------------------------------------------+
```

## 🛠️ Tech Stack

-   **Backend**: FastAPI
-   **Frontend**: Streamlit
-   **AI/Orchestration**: LangChain, LangGraph
-   **LLM Provider**: Groq (`llama-3.1-70b-versatile` by default, configurable)
-   **External API**: Google Calendar API
-   **Containerization**: Docker
-   **Deployment**: Render

## 📁 Project Structure

```
.
├── .env                  # Environment variables
├── .dockerignore         # Files to ignore in Docker build
├── Dockerfile            # Docker configuration for deployment
├── app.py                # FastAPI backend server
├── requirements.txt      # Python dependencies
├── streamlit_app.py      # Streamlit frontend application
└── src/
    ├── graph.py          # LangGraph agent definition
    ├── tool.py           # LangChain tools for Google Calendar
    └── utilities.py      # Low-level Google Calendar API functions
```

## 🚀 Getting Started

### Prerequisites

-   Python 3.11+
-   A Google Cloud project with the Google Calendar API enabled.
-   A Google Cloud Service Account with permissions to manage calendars.
-   A Groq API Key.

### 1. Clone the Repository

```
git clone https://github.com/DikshitKumar-code/Calender-agent.git
cd Calender-agent
```

### 2. Set Up Environment

Create a virtual environment and install the required dependencies.

```
python -m venv venv
source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Create a `.env` file in the project root and add your credentials.

**`.env` file:**

```
GROQ_API_KEY="your_groq_api_key"
GROQ_MODEL="openai/gpt-oss-120b"
GOOGLE_AUTH_MODE="service_account"
GOOGLE_CALENDAR_ID="your_calendar_id"
MANDATORY_ATTENDEE_EMAIL="your_email@example.com"
# Optional: set this if you use the booking spreadsheet endpoints.
BOOKING_SPREADSHEET_ID="your_google_spreadsheet_id"
```

You also need your Google Cloud Service Account JSON key.
1.  Download the `service-account.json` file from your Google Cloud project.
2.  Place it in the root directory of the project.

### 4. Run Locally

This application requires two services to run concurrently: the FastAPI backend and the Streamlit frontend.

**Terminal 1: Start the FastAPI Backend**

```
uvicorn app:app --host 127.0.0.1 --port 8001
```

**Terminal 2: Start the Streamlit UI**

```
streamlit run streamlit_app.py
```

Open your browser and navigate to `http://localhost:8501`. The Streamlit app connects to the local API on port `8001`.

## 🐳 Docker & Deployment on Render

This project is configured for easy deployment on Render using Docker.

### The Dockerfile

The `Dockerfile` creates a production-ready image that:
1.  Uses a slim Python 3.11 base image.
2.  Copies the application code.
3.  Installs dependencies from `requirements.txt`.
4.  Runs FastAPI internally on port `8001` and exposes Streamlit on the port assigned by Render (`PORT`).

### Deploying to Render

1.  **Fork this repository** to your own GitHub account.
2.  Go to the [Render Dashboard](https://dashboard.render.com/) and click **New > Web Service**.
3.  Connect your GitHub account and select your forked repository.
4.  Configure the service:
    -   **Environment**: Select `Docker`.
    -   **Name**: Give your service a name (e.g., `calendar-agent`).
    -   **Region**: Choose a region close to you.
5.  Under the **Advanced** section:
    -   **Add Environment Variables**:
        -   `GROQ_API_KEY`: Your Groq API key.
        -   `GOOGLE_AUTH_MODE`: `service_account`.
        -   `GOOGLE_CALENDAR_ID`: The ID of the calendar the app should manage.
        -   `MANDATORY_ATTENDEE_EMAIL`: Email address that should be added to created events.
        -   `BOOKING_SPREADSHEET_ID`: Google Sheets spreadsheet ID, if using booking endpoints.
    -   **Add Secret File**:
        -   **Filename**: `service-account.json`
        -   **Contents**: Paste the entire content of your `service-account.json` file.
        -   **NOTE**: The `utilities.py` file is configured to look for this secret file at `/etc/secrets/service-account.json`, which is where Render places it.
    -   The service account must have access to the Google Calendar and spreadsheet, and the Calendar API and Sheets API must be enabled in Google Cloud.
    -   Do not commit `credentials.json`, `token.json`, `service-account.json`, or `.env` to GitHub. The ignore files exclude these credentials from Git and Docker builds.
6.  Click **Create Web Service**. Render will automatically build the Docker image and deploy your application. The app uses Render's `PORT` for its public Streamlit interface; its FastAPI API is only available internally.

## 📝 API Endpoints

The FastAPI backend exposes the following endpoints:

| Method | Endpoint  | Description                               |
| :----- | :-------- | :---------------------------------------- |
| `POST` | `/invoke` | Processes user input via the LangGraph agent. |
| `GET`  | `/calendar/events` | Read-only events for a date range (`start_date` and `end_date`, ISO format). |
| `GET`  | `/health` | Health check to confirm the API is running. |

## 💡 Usage Examples

Interact with the chat UI using natural language:

-   "Create an event for 'Team Lunch' this Friday from 1 PM to 2 PM."
-   "What do I have scheduled for tomorrow morning?"
-   "Postpone today's 5 pm meeting to tomorrow 10 am"
-   "Cancel my meeting about the project review."

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for bugs, feature requests, or improvements.

1.  Fork the repository.
2.  Create a new branch (`git checkout -b feature/YourFeature`).
3.  Commit your changes (`git commit -m 'Add some feature'`).
4.  Push to the branch (`git push origin feature/YourFeature`).
5.  Open a Pull Request.

## 📜 License

This project is licensed under the MIT License. See the `LICENSE` file for details.
