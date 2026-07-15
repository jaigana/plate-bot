# CPM2 Plates Market — ER model

```mermaid
erDiagram
    USERS ||--o{ PLATES : owns
    USERS ||--o{ SALES : sells
    USERS ||--o{ SALES : buys
    USERS ||--o{ AUCTIONS : creates
    USERS ||--o{ BIDS : places
    USERS ||--o{ TRANSACTIONS : receives
    USERS ||--o{ NOTIFICATIONS : receives
    USERS ||--o{ PAYMENT_INTENTS : pays
    PLATES ||--o{ SALES : listed_as
    PLATES ||--o{ AUCTIONS : auctioned_as
    PLATES ||--o{ OWNERSHIP_HISTORY : records
    AUCTIONS ||--o{ BIDS : contains
    PAYMENT_INTENTS ||--o| STATE_EMISSION_RESERVATIONS : reserves
    BANNERS ||--o{ BOT_CARDS : displayed_on

    USERS {
        bigint id PK
        bigint telegram_id UK
        int balance_available
        int balance_frozen
        jsonb screen_stack
    }
    PLATES {
        bigint id PK
        varchar plate_number UK
        varchar country_code
        bigint owner_id FK
        varchar status
        bigint reserved_by FK
        timestamptz reserved_until
    }
    SALES {
        bigint id PK
        bigint plate_id FK
        bigint seller_id FK
        bigint buyer_id FK
        int price
        int commission
        varchar status
    }
    AUCTIONS {
        bigint id PK
        bigint plate_id FK
        bigint seller_id FK
        bigint highest_bidder_id FK
        int current_price
        timestamptz ends_at
        varchar status
    }
    BIDS {
        bigint id PK
        bigint auction_id FK
        bigint bidder_id FK
        int amount
        boolean anti_sniping_applied
    }
    PAYMENT_INTENTS {
        bigint id PK
        varchar payload UK
        bigint user_id FK
        varchar kind
        int amount
        varchar status
        varchar telegram_payment_charge_id UK
    }
```

The PostgreSQL database is the transactional source of truth. `SELECT … FOR UPDATE` locks
the affected plate, auction, payment intent, and wallet rows inside each command transaction.
Redis stores only aiogram FSM state; it never stores balances or ownership.
