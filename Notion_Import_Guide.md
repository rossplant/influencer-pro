# Notion Import Guide — Plant Schemas

## 1. Product Database

1. In Notion, create a new **Full-page database**.
2. Name it **Plant Products**.
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

## 2. Influencer Database

1. Create another full-page database named **Plant Influencers**.
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
Once Notion is connected, we can sync these databases programmatically.
