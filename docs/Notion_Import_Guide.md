# Notion Import Guide — Plantr schemas

Notion is not the Plantr database. `main.py` does not read or write Notion. Postgres (`DATABASE_URL`) is the system of record. When that variable is unset, influencer profiles stay in process memory. The steps below are only a scratch workspace if you want the field lists in Notion by hand.

## 1. Product scratch page

1. In Notion, create a scratch page. This page is not the Plantr database.
2. Name it **Plantr products**.
3. Add these properties (match `product_schema.json`):

| Property Name          | Type          | Notes                              |
|------------------------|---------------|------------------------------------|
| Title                  | Title         |                                    |
| Brand                  | Text          |                                    |
| Category               | Select        |                                    |
| Description Short      | Text          |                                    |
| Description Long       | Text          |                                    |
| Key Features           | Multi-select  |                                    |
| Target Audience        | Multi-select  |                                    |
| Occasions              | Multi-select  |                                    |
| Care Instructions      | Text          |                                    |
| Price                  | Number        |                                    |
| Inventory Status       | Select        | in_stock / low_stock / out_of_stock |
| Signal Score           | Number        | Formula or manual                  |
| Creator Talking Points | Text          |                                    |
| Common Questions       | Text          |                                    |

4. Import sample rows later from Shopify or manually.

## 2. Influencer scratch page

1. Create another scratch page named **Plantr influencers**. This page is not the Plantr database.
2. Properties from `influencer_schema.json`:

| Property Name              | Type          |
|----------------------------|---------------|
| Handle                     | Title         |
| Name                       | Text          |
| Bio                        | Text          |
| Areas of Interest          | Multi-select  |
| Expertise Level            | Text          | (or relation) |
| Preferred Promotion Styles | Multi-select  |
| Availability               | Select        |
| Restrictions               | Multi-select  |

## 3. Linking
- Add a Relation property between Products and Influencers for matching results.
- Use the matching logic in `matching_example.py` to score and store matches.

## 4. Next
Do not sync these pages from the API. A later Postgres schema should follow `product_schema.json` and `influencer_schema.json`.
