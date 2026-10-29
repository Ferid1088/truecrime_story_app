import { expect, test } from "./test-base";

const PLAN = {
  graph_id: 7,
  scene_count: 2,
  stale_count: 1,
  scenes: [
    {
      scene_key: "s1",
      scene_order: 1,
      mode: "direct_scenes",
      aim: "Establish the gun's origin",
      change_in_understanding: "The viewer learns the gun came from the house",
      why_selected: "A fact card states the confirmed detail plainly",
      expected_audience_gain: "Know the weapon's type and origin",
      covered_unit_keys: ["B01-1"],
      active_question_keys: ["q-location"],
      active_entity_keys: ["gun"],
      alternatives_considered: [],
      stale: false,
      stale_reason: null,
      actions: [
        {
          action_key: "s1-a1", tool_id: "fact_card", purpose: "State the gun's origin",
          content_bindings: ["c-origin"], reuse_reason: null,
        },
      ],
    },
    {
      scene_key: "s2",
      scene_order: 2,
      mode: "direct_scenes",
      aim: "Raise the open question of the gun's location",
      change_in_understanding: "The viewer learns the location is unresolved",
      why_selected: "A known/unknown panel pairs the known origin with the open question",
      expected_audience_gain: "Understand what remains unknown",
      covered_unit_keys: ["B02-1"],
      active_question_keys: ["q-location"],
      active_entity_keys: ["gun"],
      alternatives_considered: [],
      stale: true,
      stale_reason: "owner correction: narration revised",
      actions: [
        {
          action_key: "s2-a1", tool_id: "unknowns", purpose: "Show the open location question",
          content_bindings: ["q-location"], reuse_reason: null,
        },
      ],
    },
  ],
  design_reviews: [],
};

test("Detective tab shows scenes, their tool choices, and stale state", async ({ page }) => {
  await page.route("**/api/cases/1/detective/plan", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PLAN) }));

  await page.goto("/documentary/1?tab=Detective");
  await expect(page.getByTestId("detective-tab")).toBeVisible();
  await expect(page.getByTestId("detective-totals")).toContainText("scenes: 2");
  await expect(page.getByTestId("detective-totals")).toContainText("stale: 1");

  const sceneOne = page.getByTestId("detective-scene-s1");
  await expect(sceneOne).toContainText("Establish the gun's origin");
  await expect(sceneOne).toContainText("fact_card");
  await expect(sceneOne.getByTestId("scene-stale-badge")).toHaveCount(0);

  const sceneTwo = page.getByTestId("detective-scene-s2");
  await expect(sceneTwo.getByTestId("scene-stale-badge")).toContainText("owner correction");
});
