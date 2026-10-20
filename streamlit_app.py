import streamlit as st
import requests
import json
import csv
import io
import calendar as month_calendar
from datetime import date, datetime, timedelta
import pytz

# uvicorn app:app --host 0.0.0.0 --port 8001
# streamlit run streamlit_app.py
# Book a Bar Mitzvah on Monday at 07:00 for Cohen, name David. Add attendee david.family@example.com.

st.set_page_config(page_title="Calendar AI Assistant", page_icon="📅")
st.title("📅 AI Calendar Booking Assistant")

API_URL = "http://localhost:8001/invoke"
CALENDAR_EVENTS_API_URL = "http://localhost:8001/calendar/events"
BOOKING_API_URL = "http://localhost:8001/bookings/submit"
BOOKING_LIST_API_URL = "http://localhost:8001/bookings/list"
BOOKING_MARK_PAID_API_URL = "http://localhost:8001/bookings/mark-paid"

show_debug = st.sidebar.checkbox("Show debug in chat", value=True)


def _append_debug_message(title: str, data: dict):
    debug_text = f"### DEBUG: {title}\n```json\n{json.dumps(data, indent=2, default=str)}\n```"
    st.session_state.chat_history.append({"role": "assistant", "content": debug_text})


def _is_allowed_date(category: str, day: date) -> bool:
    weekday = day.weekday()  # Monday=0 ... Sunday=6
    if category == "BAR_MITZVAH_GROOM":
        return weekday in (0, 3, 5)
    return weekday != 5


def _get_allowed_dates(category: str, start_day: date, days_ahead: int = 120):
    return [
        start_day + timedelta(days=offset)
        for offset in range(days_ahead + 1)
        if _is_allowed_date(category, start_day + timedelta(days=offset))
    ]


def _format_date_he(day: date) -> str:
    weekday_he = {
        0: "יום שני",
        1: "יום שלישי",
        2: "יום רביעי",
        3: "יום חמישי",
        4: "יום שישי",
        5: "שבת",
        6: "יום ראשון",
    }
    return f"{weekday_he[day.weekday()]}, {day.strftime('%d/%m/%Y')}"


def _build_booking_payload(
    category,
    selected_date,
    event_type,
    priestly_status,
    slot_type,
    catering_type,
    contact_name,
    contact_phone,
    contact_email,
    amount_due,
    notes,
):
    payload = {
        "intent": "BOOKING_REQUEST",
        "version": "srs_v2.0",
        "requestTimestamp": selected_date.isoformat(),
        "booking": {
            "date": selected_date.isoformat(),
            "contactName": contact_name,
            "contactPhone": contact_phone,
            "contactEmail": contact_email,
            "amountDue": float(amount_due or 0),
            "notes": notes,
        },
    }

    if category == "BAR_MITZVAH_GROOM":
        payload["booking"].update(
            {
                "category": "BAR_GROOM",
                "eventType": event_type,
                "slotType": slot_type,
                "priestlyStatus": priestly_status,
                "cateringType": None,
            }
        )
    else:
        payload["booking"].update(
            {
                "category": "AZKARA",
                "eventType": None,
                "slotType": "POST_ARVIT",
                "priestlyStatus": None,
                "cateringType": catering_type,
            }
        )

    return payload


def _safe_parse_date(value) -> date | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            continue
    return None


def _safe_amount(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _period_matches(day: date | None, period: str, anchor: date) -> bool:
    if not day:
        return False
    if period == "שבוע":
        return day.isocalendar()[:2] == anchor.isocalendar()[:2]
    if period == "חודש":
        return day.year == anchor.year and day.month == anchor.month
    return day.year == anchor.year


def _rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _event_day(event: dict) -> date | None:
    start = event.get("start", {})
    if start.get("date"):
        return date.fromisoformat(start["date"])
    if not start.get("dateTime"):
        return None
    start_datetime = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
    if not start_datetime.tzinfo:
        start_datetime = pytz.timezone("Asia/Jerusalem").localize(start_datetime)
    if start_datetime.tzinfo:
        start_datetime = start_datetime.astimezone(pytz.timezone("Asia/Jerusalem"))
    return start_datetime.date()


def _event_time(event: dict) -> str:
    start_datetime = event.get("start", {}).get("dateTime")
    if not start_datetime:
        return "All day"
    parsed = datetime.fromisoformat(start_datetime.replace("Z", "+00:00"))
    if not parsed.tzinfo:
        parsed = pytz.timezone("Asia/Jerusalem").localize(parsed)
    if parsed.tzinfo:
        parsed = parsed.astimezone(pytz.timezone("Asia/Jerusalem"))
    return parsed.strftime("%H:%M")


def _shift_month(month: date, offset: int) -> date:
    month_index = month.year * 12 + month.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


# Initialize chat history
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "calendar_month" not in st.session_state:
    st.session_state.calendar_month = date.today().replace(day=1)

tab_chat, tab_calendar, tab_booking, tab_gabay = st.tabs(
    ["Assistant Chat", "Calendar", "Book a Day (Form)", "Gabay Control"]
)

with tab_chat:
    with st.form("user_input_form", clear_on_submit=True):
        user_input = st.text_input(
            "Enter your request:",
            placeholder="e.g., Postpone today's meeting to tomorrow 10 am",
        )
        submitted = st.form_submit_button("Send")

    if submitted and user_input.strip():
        st.session_state.chat_history.append({"role": "user", "content": user_input})

        try:
            with st.spinner("Thinking..."):
                response = requests.post(API_URL, json={"user_input": user_input}, timeout=60)
                response.raise_for_status()
                result = response.json()

                if show_debug:
                    _append_debug_message(
                        "Chat Request",
                        {
                            "endpoint": API_URL,
                            "request": {"user_input": user_input},
                            "response": result,
                        },
                    )

                if result["status"] == "success":
                    full_result = result["result"]
                    if isinstance(full_result, dict) and "messages" in full_result:
                        final_message = ""
                        for msg in reversed(full_result["messages"]):
                            if msg.get("type") == "ai" and msg.get("content"):
                                final_message = msg["content"]
                                break
                    else:
                        final_message = str(full_result)

                    st.session_state.chat_history.append({"role": "assistant", "content": final_message})
                else:
                    st.session_state.chat_history.append({"role": "assistant", "content": f"❌ Error: {result['message']}"})
        except requests.exceptions.JSONDecodeError:
            if show_debug:
                _append_debug_message(
                    "Chat JSONDecodeError",
                    {
                        "endpoint": API_URL,
                        "error": "Backend did not return valid JSON",
                    },
                )
            st.session_state.chat_history.append(
                {
                    "role": "assistant",
                    "content": "❌ Backend did not return JSON. Check that FastAPI is running on http://localhost:8001.",
                }
            )
        except requests.exceptions.RequestException as e:
            if show_debug:
                _append_debug_message(
                    "Chat RequestException",
                    {
                        "endpoint": API_URL,
                        "error": str(e),
                    },
                )
            st.session_state.chat_history.append({"role": "assistant", "content": f"❌ Request failed: {str(e)}"})
        except Exception as e:
            if show_debug:
                _append_debug_message(
                    "Chat Exception",
                    {
                        "endpoint": API_URL,
                        "error": str(e),
                    },
                )
            st.session_state.chat_history.append({"role": "assistant", "content": f"❌ Request failed: {str(e)}"})

    for message in reversed(st.session_state.chat_history):
        role = message["role"]
        content = message["content"]
        with st.chat_message("🧑‍💻 User" if role == "user" else "🤖 Assistant"):
            st.markdown(content)

with tab_calendar:
    selected_month = st.session_state.calendar_month
    previous_col, title_col, next_col = st.columns([1, 3, 1])
    with previous_col:
        if st.button("← Previous", key="calendar_previous"):
            st.session_state.calendar_month = _shift_month(selected_month, -1)
            st.rerun()
    with title_col:
        st.subheader(selected_month.strftime("%B %Y"))
    with next_col:
        if st.button("Next →", key="calendar_next"):
            st.session_state.calendar_month = _shift_month(selected_month, 1)
            st.rerun()

    month_start = selected_month.replace(day=1)
    next_month = _shift_month(month_start, 1)
    try:
        calendar_response = requests.get(
            CALENDAR_EVENTS_API_URL,
            params={
                "start_date": month_start.isoformat(),
                "end_date": next_month.isoformat(),
            },
            timeout=30,
        )
        calendar_response.raise_for_status()
        calendar_result = calendar_response.json()
        if calendar_result.get("status") != "success":
            st.error(calendar_result.get("message", "Could not load calendar events."))
        else:
            events_by_day: dict[date, list[dict]] = {}
            for event in calendar_result.get("events", []):
                event_day = _event_day(event)
                if event_day:
                    events_by_day.setdefault(event_day, []).append(event)

            weekday_columns = st.columns(7)
            for column, weekday in zip(
                weekday_columns,
                ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
            ):
                column.markdown(f"**{weekday}**")

            for week in month_calendar.Calendar(firstweekday=6).monthdayscalendar(
                selected_month.year, selected_month.month
            ):
                day_columns = st.columns(7)
                for column, day_number in zip(day_columns, week):
                    with column.container(border=True):
                        if day_number == 0:
                            st.write(" ")
                            continue
                        current_day = date(
                            selected_month.year, selected_month.month, day_number
                        )
                        st.markdown(f"**{day_number}**")
                        for event in events_by_day.get(current_day, []):
                            title = str(event.get("summary") or "Busy")
                            st.caption(f"{_event_time(event)} · {title}")
    except requests.exceptions.RequestException as error:
        st.error(f"Could not load calendar events: {error}")
    except (ValueError, TypeError, KeyError) as error:
        st.error(f"Calendar response contained invalid event data: {error}")

with tab_booking:
    st.markdown(
        """
        <style>
        /* Hebrew booking UI in RTL */
        [data-testid="stVerticalBlock"] {
            direction: rtl;
            text-align: right;
        }

        /* Keep technical/code output readable in LTR */
        pre, code, .stCodeBlock {
            direction: ltr !important;
            text-align: left !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("טופס הזמנה מובנה")
    st.caption("הטופס מוצג בעברית, אבל נשלח למודל בפורמט JSON תקני באנגלית.")

    category_labels_he = {
        "BAR_MITZVAH_GROOM": "בר מצווה / חתן",
        "AZKARA": "אזכרה (פרגולה)",
    }
    event_type_labels_he = {
        "BAR_MITZVAH": "בר מצווה",
        "GROOM": "חתן",
    }
    priestly_status_labels_he = {
        "COHEN": "כהן",
        "ISRAEL": "ישראל",
        "LEVI": "לוי",
    }
    azkara_type_labels_he = {
        "BLESSINGS_ONLY": "ברכות בלבד",
        "CATERING": "קייטרינג",
    }

    category = st.selectbox(
        "קטגוריית הזמנה",
        options=["BAR_MITZVAH_GROOM", "AZKARA"],
        format_func=lambda x: category_labels_he[x],
    )

    allowed_dates = _get_allowed_dates(category, date.today())
    selected_date = st.selectbox(
        "תאריך הזמנה",
        options=allowed_dates,
        format_func=_format_date_he,
        help=(
            "בר מצווה/חתן: רק בימים שני, חמישי ושבת."
            if category == "BAR_MITZVAH_GROOM"
            else "אזכרה: בכל יום למעט שבת."
        ),
    )

    with st.form("booking_form", clear_on_submit=False):
        event_type = None
        priestly_status = None
        slot_type = None
        catering_type = None

        if category == "BAR_MITZVAH_GROOM":
            service_time = st.selectbox("שעת תפילה", options=["07:00", "08:00"])
            slot_type = "SLOT_0700" if service_time == "07:00" else "SLOT_0800"
            event_type = st.selectbox(
                "סוג אירוע",
                options=["BAR_MITZVAH", "GROOM"],
                format_func=lambda x: event_type_labels_he[x],
            )
            priestly_status = st.selectbox(
                "מעמד כהונה",
                options=["COHEN", "ISRAEL", "LEVI"],
                format_func=lambda x: priestly_status_labels_he[x],
            )
            st.info("בר מצווה/חתן: שני, חמישי או שבת בלבד, בשעה 07:00 או 08:00.")
        else:
            slot_type = "POST_ARVIT"
            catering_type = st.selectbox(
                "סוג אזכרה",
                options=["BLESSINGS_ONLY", "CATERING"],
                format_func=lambda x: azkara_type_labels_he[x],
            )
            st.text_input("שעה", value="ערב (אחרי ערבית)", disabled=True)
            st.info("אזכרה: בכל יום למעט שבת. השעה קבועה לאחר ערבית.")

        col1, col2 = st.columns(2)
        with col1:
            contact_name = st.text_input("שם איש קשר")
            contact_phone = st.text_input("טלפון איש קשר")
        with col2:
            contact_email = st.text_input("אימייל איש קשר")
            amount_due = st.number_input("מחיר", min_value=0.0, step=50.0, value=0.0)
            notes = st.text_input("הערות (אופציונלי)")

        submitted_booking = st.form_submit_button("שליחת טופס הזמנה")

    if submitted_booking:
        if not contact_name or not contact_phone or not contact_email:
            st.error("יש למלא שם, טלפון ואימייל של איש הקשר.")
        else:
            payload = _build_booking_payload(
                category=category,
                selected_date=selected_date,
                event_type=event_type,
                priestly_status=priestly_status,
                slot_type=slot_type,
                catering_type=catering_type,
                contact_name=contact_name,
                contact_phone=contact_phone,
                contact_email=contact_email,
                amount_due=amount_due,
                notes=notes,
            )

            st.markdown("### JSON שנשלח למודל")
            st.code(json.dumps(payload, indent=2), language="json")

            st.session_state.chat_history.append(
                {
                    "role": "user",
                    "content": "Submitted structured booking form (BOOKING_REQUEST JSON).",
                }
            )

            try:
                with st.spinner("Submitting to model..."):
                    response = requests.post(BOOKING_API_URL, json=payload, timeout=60)
                    response.raise_for_status()
                    result = response.json()

                    if show_debug:
                        _append_debug_message(
                            "Booking Request",
                            {
                                "endpoint": BOOKING_API_URL,
                                "request": payload,
                                "response": result,
                            },
                        )

                    if result.get("status") == "success":
                        final_message = result.get("answer", "Booking accepted.")
                        st.session_state.chat_history.append({"role": "assistant", "content": final_message})
                        st.success("טופס ההזמנה נשלח בהצלחה.")
                    elif result.get("status") == "blocked":
                        policy_message = result.get("message", "Booking blocked by policy constraints.")
                        st.session_state.chat_history.append({"role": "assistant", "content": policy_message})
                        st.warning("הבקשה תקינה אך חסומה לפי אילוצי מדיניות/זמינות.")
                    else:
                        error_message = result.get("message", "Unknown error")
                        st.session_state.chat_history.append({"role": "assistant", "content": f"❌ Error: {error_message}"})
                        st.error(f"שגיאת שרת: {error_message}")
            except Exception as e:
                if show_debug:
                    _append_debug_message(
                        "Booking Exception",
                        {
                            "endpoint": BOOKING_API_URL,
                            "request": payload,
                            "error": str(e),
                        },
                    )
                st.session_state.chat_history.append({"role": "assistant", "content": f"❌ Request failed: {str(e)}"})
                st.error(f"שליחת הטופס נכשלה: {str(e)}")


with tab_gabay:
    st.subheader("ניהול גבאי")
    st.caption("עדכון סטטוס תשלום ישירות מתוך הטבלה")

    try:
        list_resp = requests.get(BOOKING_LIST_API_URL, timeout=30)
        list_resp.raise_for_status()
        list_data = list_resp.json()

        if list_data.get("status") == "success":
            bookings = list_data.get("bookings", [])
            st.write(f"נמצאו {len(bookings)} הזמנות")

            with st.expander("סטטיסטיקה לגבאי (שבוע / חודש / שנה)", expanded=False):
                period_col, anchor_col = st.columns([1, 1])
                with period_col:
                    stats_period = st.selectbox("תקופת ניתוח", options=["שבוע", "חודש", "שנה"], key="gabay_stats_period")
                with anchor_col:
                    anchor_day = st.date_input("תאריך ייחוס", value=date.today(), key="gabay_stats_anchor")

                stats_bookings = []
                for booking in bookings:
                    event_day = _safe_parse_date(booking.get("eventDate"))
                    if _period_matches(event_day, stats_period, anchor_day):
                        stats_bookings.append(booking)

                total_amount = sum(_safe_amount(b.get("amountDue")) for b in stats_bookings)
                paid_amount = sum(
                    _safe_amount(b.get("amountDue"))
                    for b in stats_bookings
                    if str(b.get("paidStatus", "")).upper() == "PAID"
                )
                unpaid_amount = total_amount - paid_amount
                paid_count = sum(1 for b in stats_bookings if str(b.get("paidStatus", "")).upper() == "PAID")
                unpaid_count = len(stats_bookings) - paid_count
                collection_rate = (paid_count / len(stats_bookings) * 100) if stats_bookings else 0

                by_event = {}
                by_weekday = {"שני": 0, "שלישי": 0, "רביעי": 0, "חמישי": 0, "שישי": 0, "שבת": 0, "ראשון": 0}
                for booking in stats_bookings:
                    event_type = str(booking.get("eventType") or "UNKNOWN")
                    by_event[event_type] = by_event.get(event_type, 0) + 1

                    event_day = _safe_parse_date(booking.get("eventDate"))
                    if event_day:
                        day_name = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"][event_day.weekday()]
                        by_weekday[day_name] += 1

                event_rows = [{"סוג אירוע": k, "כמות": v} for k, v in sorted(by_event.items(), key=lambda x: x[1], reverse=True)]
                weekday_rows = [{"יום": day_name, "כמות": count} for day_name, count in by_weekday.items()]

                st.caption(f"בתקופה שנבחרה: {len(stats_bookings)} הזמנות")
                k1, k2, k3 = st.columns(3)
                k1.metric("אחוז גבייה", f"{collection_rate:.1f}%")
                k2.metric("נגבה", f"₪{paid_amount:,.0f}")
                k3.metric("יתרה", f"₪{unpaid_amount:,.0f}")

                chart_col1, chart_col2 = st.columns(2)
                with chart_col1:
                    st.markdown("**סטטוס תשלום**")
                    pie_rows = [
                        {"סטטוס": "PAID", "כמות": paid_count},
                        {"סטטוס": "UNPAID", "כמות": unpaid_count},
                    ]
                    st.vega_lite_chart(
                        pie_rows,
                        {
                            "mark": {"type": "arc", "innerRadius": 45},
                            "encoding": {
                                "theta": {"field": "כמות", "type": "quantitative"},
                                "color": {
                                    "field": "סטטוס",
                                    "type": "nominal",
                                    "scale": {"range": ["#16a34a", "#ef4444"]},
                                },
                                "tooltip": [
                                    {"field": "סטטוס", "type": "nominal"},
                                    {"field": "כמות", "type": "quantitative"},
                                ],
                            },
                        },
                        use_container_width=True,
                    )

                with chart_col2:
                    st.markdown("**הזמנות לפי סוג אירוע**")
                    st.vega_lite_chart(
                        event_rows,
                        {
                            "mark": {"type": "bar", "cornerRadiusTopLeft": 6, "cornerRadiusTopRight": 6},
                            "encoding": {
                                "x": {
                                    "field": "סוג אירוע",
                                    "type": "ordinal",
                                    "sort": "-y",
                                },
                                "y": {"field": "כמות", "type": "quantitative"},
                                "color": {"value": "#2563eb"},
                                "tooltip": [
                                    {"field": "סוג אירוע", "type": "ordinal"},
                                    {"field": "כמות", "type": "quantitative"},
                                ],
                            },
                        },
                        use_container_width=True,
                    )

                export_rows = []
                for booking in stats_bookings:
                    export_rows.append(
                        {
                            "booking_id": booking.get("bookingId", ""),
                            "contact_name": booking.get("contactName", ""),
                            "event_date": booking.get("eventDate", ""),
                            "event_type": booking.get("eventType", ""),
                            "amount_due": booking.get("amountDue", ""),
                            "paid_status": booking.get("paidStatus", ""),
                        }
                    )

                export_col1, export_col2 = st.columns(2)
                with export_col1:
                    st.download_button(
                        "הורדת נתוני תקופה (CSV)",
                        data=_rows_to_csv(export_rows),
                        file_name=f"gabay_stats_{stats_period}_{anchor_day.isoformat()}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                with export_col2:
                    summary_payload = {
                        "period": stats_period,
                        "anchorDate": anchor_day.isoformat(),
                        "totalBookings": len(stats_bookings),
                        "paidBookings": paid_count,
                        "unpaidBookings": unpaid_count,
                        "totalAmount": total_amount,
                        "paidAmount": paid_amount,
                        "unpaidAmount": unpaid_amount,
                        "collectionRatePercent": round(collection_rate, 1),
                        "byEventType": event_rows,
                        "byWeekday": weekday_rows,
                    }
                    st.download_button(
                        "הורדת סיכום סטטיסטי (JSON)",
                        data=json.dumps(summary_payload, ensure_ascii=False, indent=2),
                        file_name=f"gabay_stats_summary_{stats_period}_{anchor_day.isoformat()}.json",
                        mime="application/json",
                        use_container_width=True,
                    )

            if bookings:
                summary_rows = []
                original_status_by_id = {}
                for booking in bookings:
                    paid_status = (booking.get("paidStatus") or "").upper()
                    bid = str(booking.get("bookingId", ""))
                    original_status_by_id[bid] = paid_status
                    summary_rows.append(
                        {
                            "booking_id": bid,
                            "איש קשר": booking.get("contactName", ""),
                            "תאריך": booking.get("eventDate", ""),
                            "סוג אירוע": booking.get("eventType", ""),
                            "סכום": booking.get("amountDue", ""),
                            "סטטוס תשלום": paid_status,
                        }
                    )

                edited_rows = st.data_editor(
                    summary_rows,
                    use_container_width=True,
                    hide_index=True,
                    key="gabay_paid_status_editor",
                    column_config={
                        "סטטוס תשלום": st.column_config.SelectboxColumn(
                            "סטטוס תשלום",
                            options=["UNPAID", "PAID"],
                            required=True,
                        ),
                    },
                    disabled=["booking_id", "איש קשר", "תאריך", "סוג אירוע", "סכום"],
                )

                if st.button("שמור שינויים", use_container_width=True):
                    changed_rows = []
                    for row in edited_rows:
                        bid = str(row.get("booking_id", ""))
                        new_status = (row.get("סטטוס תשלום") or "UNPAID").upper()
                        old_status = original_status_by_id.get(bid, "UNPAID")
                        if bid and new_status != old_status:
                            changed_rows.append((bid, new_status))

                    if not changed_rows:
                        st.info("לא נמצאו שינויים לשמירה.")
                    else:
                        success_count = 0
                        try:
                            for bid, new_status in changed_rows:
                                update_resp = requests.post(
                                    BOOKING_MARK_PAID_API_URL,
                                    json={"bookingId": bid, "isPaid": new_status == "PAID"},
                                    timeout=30,
                                )
                                update_resp.raise_for_status()
                                update_data = update_resp.json()
                                if update_data.get("status") == "success":
                                    success_count += 1
                                else:
                                    st.error(f"העדכון נכשל עבור {bid}: {update_data.get('message', 'Unknown error')}")

                            if success_count:
                                st.success(f"נשמרו {success_count} עדכונים בהצלחה.")
                                st.rerun()
                        except Exception as e:
                            st.error(f"שמירת שינויים נכשלה: {str(e)}")
            else:
                st.info("אין נתונים להצגה.")
        else:
            st.error(f"שגיאה בקריאת הזמנות: {list_data.get('message', 'Unknown error')}")
    except Exception as e:
        st.error(f"שגיאה בקריאת הזמנות: {str(e)}")