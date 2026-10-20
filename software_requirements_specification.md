# Software Requirements Specification (SRS)

## Synagogue & Event Hall Booking System (v2.0)

---

## 1. System Overview

The system is an automated booking and management platform designed to handle reservations for religious events (Bar Mitzvahs, Groom Aliyahs) and Memorial Services (Azkarot) at a synagogue/hall facility. It automatically enforces complex capacity constraints, global quotas, and strict Halachic rules regarding Torah reading honors (priestly status tracking for Cohen, Levi, and Israel).

---

## 2. Core Entities & Data Models

### 2.1 Enumerations

* **PriestlyStatus:** `COHEN`, `LEVI`, `ISRAEL`
* **WeekdaySlotType:** `SLOT_0700` (7:00 AM), `SLOT_0800` (8:00 AM)
* **BarMitzvahEventType:** `BAR_MITZVAH`, `GROOM`
* **AzkaraCateringType:** `BLESSINGS_ONLY`, `CATERING`

### 2.2 Event Object Models

#### A. Bar Mitzvah / Groom Reservation

```json
{
  "id": "string",
  "date": "YYYY-MM-DD",
  "eventType": "BAR_MITZVAH | GROOM",
  "slotType": "SLOT_0700 | SLOT_0800 | SHABBAT_MORNING",
  "priestlyStatus": "COHEN | LEVI | ISRAEL",
  "contactName": "string",
  "contactPhone": "string",
  "contactEmail": "string"
}

```

#### B. Azkara (Memorial Service) Reservation

```json
{
  "id": "string",
  "date": "YYYY-MM-DD",
  "cateringType": "BLESSINGS_ONLY | CATERING",
  "slotType": "POST_ARVIT",
  "contactName": "string",
  "contactPhone": "string",
  "contactEmail": "string"
}

```

---

## 3. Business Logic & Validation Rules

### 3.1 Weekday Bar Mitzvah Rules (Monday & Thursday)

* **Allowed Days:** Mondays and Thursdays only.
* **Available Slots per Day:**
* Slot A: 07:00 AM (`SLOT_0700`)
* Slot B: 08:00 AM (`SLOT_0800`)


* **Capacity Constraints per Slot:**
* Maximum capacity: 2 Bar Mitzvahs per slot.
* **Rule 1 (Single Reservation):** Any priestly status (`COHEN`, `LEVI`, `ISRAEL`) can book an open slot.
* **Rule 2 (Dual Reservation Eligibility):** A second reservation in the same slot is allowed **ONLY IF** the combination of statuses does not conflict with weekday Torah reading limits (which support only 3 standard aliya calls: Cohen, Levi, Israel):
* `COHEN` + `ISRAEL` $\rightarrow$ **ALLOWED**
* `LEVI` + `ISRAEL` $\rightarrow$ **ALLOWED**
* `COHEN` + `COHEN` $\rightarrow$ **BLOCKED** (`STATUS_CONFLICT`)
* `LEVI` + `LEVI` $\rightarrow$ **BLOCKED** (`STATUS_CONFLICT`)
* `ISRAEL` + `ISRAEL` $\rightarrow$ **BLOCKED** (`STATUS_CONFLICT` - Weekdays lack extra slots for dual Israel calls without custom override).
* `COHEN` + `LEVI` $\rightarrow$ **BLOCKED** (`STATUS_CONFLICT` - Both require primary honors when no Israel is present).





---

### 3.2 Shabbat Rules (Saturday Morning)

* **Allowed Days:** Saturdays only.
* **Slot:** Saturday Morning Service (`SHABBAT_MORNING`).
* **Global Shabbat Quotas:**
* Max Bar Mitzvahs: **2**
* Max Grooms (`GROOM`): **1**
* Total combined max events on Shabbat: **3** (2 Bar Mitzvahs + 1 Groom).


* **Validation Rules:**
* Reject any `GROOM` booking if count(`GROOM`) $\ge 1$.
* Reject any `BAR_MITZVAH` booking if count(`BAR_MITZVAH`) $\ge 2$.
* **Halachic Priestly Status Quotas on Shabbat:** Shabbat features 7 base aliyot plus Maftir, allowing multiple Israelites but strictly limiting primary Torah holders:
* Max `COHEN` count across all Shabbat celebrants $\le 1$. (Exceeding triggers `STATUS_CONFLICT`).
* Max `LEVI` count across all Shabbat celebrants $\le 1$. (Exceeding triggers `STATUS_CONFLICT`).
* Multiple `ISRAEL` celebrants are **ALLOWED** (up to the total quota of 3 celebrants, as Shabbat provides enough Israel aliyot: slots 3 through 7).





---

### 3.3 Pergola Azkara Rules (Weekdays, Post-Arvit)

* **Allowed Days:** Sunday through Thursday.
* **Slot:** Evening (After Arvit prayer).
* **Catering Constraints:**
* **If `CATERING` is selected:**
* Max reservations for that date = **1**.
* Blocks all other Azkara bookings for the evening.


* **If `BLESSINGS_ONLY` is selected:**
* Max reservations for that date = **3** (Minimum capacity limit: 2, Maximum: 3).
* Can only co-exist with other `BLESSINGS_ONLY` reservations.
* If an existing booking on that date is `CATERING`, block new `BLESSINGS_ONLY` bookings.





---

## 4. UI/UX Flow & Form State Machine

```
[Start Booking]
        │
        ├──► Select Category
        │        ├──► "Bar Mitzvah / Groom"
        │        └──► "Azkara (Pergola)"
        │
        ├──► Dynamic Date Picker (Calendar)
        │        ├── If "Bar Mitzvah": Enable Mon, Thu, Sat
        │        └── If "Azkara": Enable Sun-Thu
        │
        ├──► Slot & Category Selection
        │        ├── If Mon/Thu selected:
        │        │    └── Select Slot (7:00 AM / 8:00 AM)
        │        │    └── Select Status (Cohen / Levi / Israel)
        │        │
        │        ├── If Shabbat selected:
        │        │    └── Select Event Type (Bar Mitzvah / Groom)
        │        │    └── Select Status (Cohen / Levi / Israel)
        │        │
        │        └── If Azkara selected:
        │             └── Select Type (Blessings Only / Catering)
        │
        ├──► Server Validation (Check Availability & Conflicts)
        │        ├── PASS ──► Prompt User Details ──► Confirm & Reserve
        │        └── FAIL ──► Display Specific Error Message

```

---

## 5. API Endpoint Specifications (Pseudo-Code)

### `POST /api/bookings/check-availability`

**Request Payload:**

```json
{
  "date": "2026-11-02",
  "category": "BAR_MITZVAH",
  "slotType": "SLOT_0700",
  "priestlyStatus": "COHEN",
  "eventType": "BAR_MITZVAH",
  "cateringType": null
}

```

**Response (Success):**

```json
{
  "available": true,
  "message": "Slot available for booking."
}

```

**Response (Conflict Failure):**

```json
{
  "available": false,
  "errorCode": "STATUS_CONFLICT",
  "message": "Cannot book two Cohens in the same minyan slot. Please select another slot or date."
}

```

---

## 6. Error & Conflict Messages

1. `SLOT_FULL`: "The selected time slot has reached its maximum capacity."
2. `STATUS_CONFLICT`: "Due to Torah reading requirements, this status combination (e.g., duplicate Cohen/Levi) cannot be booked together in the same minyan. Please choose a different slot or date."
3. `GROOM_LIMIT_REACHED`: "A groom is already booked for this Shabbat. Maximum limit is 1 groom per Shabbat."
4. `CATERING_EXCLUSIVE`: "A catering Azkara is already booked for this evening. No additional bookings allowed."
5. `PERGOLA_FULL`: "Maximum capacity for blessings-only Azkarot (3) reached for this evening."