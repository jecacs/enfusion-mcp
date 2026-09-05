/**
 * SPDX-License-Identifier: MIT
 * Adapted JsonApiStruct/NetApiHandler patterns from the MIT-licensed upstream:
 * https://github.com/steffenbk/enfusion-mcp-BK
 * Upstream commit: 0acfa884228477043c6b4c2b8c7f0c270d648398
 * See the source repository NOTICE.md for complete attribution.
 *
 * Staged narrow vegetation apply/reconcile endpoint.
 * UNVERIFIED_LIVE until ValidateScripts and the explicit live acceptance flow.
 * APIFunc: RJMCP_VegetationApply
 *
 * The compiled production allowlist is intentionally empty at Checkpoint C,
 * and MUTATION_IMPLEMENTATION_VALIDATED is independently false. Create remains
 * unreachable until the catalog is permissioned and the implementation has
 * compiled and passed the explicit live create/reconcile/Undo validation flow.
 */

class RJMCP_VegetationApplyRequest : JsonApiStruct
{
	string mode;
	string planId;
	string operationId;
	string bridgeBuildId;
	string catalogHash;
	string worldPath;
	int subscene;
	string targetLayer;
	int count;
	float minSpacingM;
	float maxSlopeDeg;
	float scaleMin;
	float scaleMax;
	float terrainYEpsilon;
	ref array<string> prefabs;
	ref array<string> entityNames;
	ref array<float> x;
	ref array<float> y;
	ref array<float> z;
	ref array<float> yaw;
	ref array<float> scale;

	void RJMCP_VegetationApplyRequest()
	{
		RegV("mode");
		RegV("planId");
		RegV("operationId");
		RegV("bridgeBuildId");
		RegV("catalogHash");
		RegV("worldPath");
		RegV("subscene");
		RegV("targetLayer");
		RegV("count");
		RegV("minSpacingM");
		RegV("maxSlopeDeg");
		RegV("scaleMin");
		RegV("scaleMax");
		RegV("terrainYEpsilon");
		prefabs = {};
		entityNames = {};
		x = {};
		y = {};
		z = {};
		yaw = {};
		scale = {};
		RegV("prefabs");
		RegV("entityNames");
		RegV("x");
		RegV("y");
		RegV("z");
		RegV("yaw");
		RegV("scale");
	}
}

class RJMCP_VegetationApplyResponse : JsonApiStruct
{
	string status;
	string errorCode;
	string message;
	string bridgeProtocolVersion;
	string bridgeBuildId;
	string catalogHash;
	string planId;
	string operationId;
	string mode;
	string state;
	int expectedCount;
	int matchingCount;
	int createdCount;
	bool rollbackVerified;
	ref array<string> m_aEntityNames;

	void RJMCP_VegetationApplyResponse()
	{
		RegV("status");
		RegV("errorCode");
		RegV("message");
		RegV("bridgeProtocolVersion");
		RegV("bridgeBuildId");
		RegV("catalogHash");
		RegV("planId");
		RegV("operationId");
		RegV("mode");
		RegV("state");
		RegV("expectedCount");
		RegV("matchingCount");
		RegV("createdCount");
		RegV("rollbackVerified");
		bridgeProtocolVersion = "rjmcp-bridge-v1";
		bridgeBuildId = "rjmcp-bridge-v1-unverified-live";
		catalogHash = "09acc4254f9f2adfc5b339b1160006526f54d3668c2016442d58609016b1d596";
		state = "REJECTED";
		m_aEntityNames = {};
	}

	override void OnPack()
	{
		StartArray("entityNames");
		for (int i = 0; i < m_aEntityNames.Count(); i++)
			ItemString(m_aEntityNames[i]);
		EndArray();
	}
}

class RJMCP_VegetationApply : NetApiHandler
{
	static const string ALLOWED_WORLD = "$thenewRJ:rj.ent";
	static const string BRIDGE_BUILD_ID = "rjmcp-bridge-v1-unverified-live";
	static const string CATALOG_HASH = "09acc4254f9f2adfc5b339b1160006526f54d3668c2016442d58609016b1d596";
	// This gate must remain false until the staged source compiles and the exact
	// create/reconcile/Undo workflow is explicitly validated in live Workbench.
	static const bool MUTATION_IMPLEMENTATION_VALIDATED = false;
	static const float DEG_TO_RAD = 0.017453292519943295;
	static const float TRANSFORM_EPSILON = 0.01;

	static void Fail(
		RJMCP_VegetationApplyResponse response,
		string code,
		string text,
		string state = "REJECTED"
	)
	{
		response.status = "error";
		response.errorCode = code;
		response.message = text;
		response.state = state;
	}

	static bool IsFiniteBounded(float value)
	{
		return value == value && Math.AbsFloat(value) <= 1000000.0;
	}

	static bool IsFiniteVector(vector value)
	{
		return IsFiniteBounded(value[0])
			&& IsFiniteBounded(value[1])
			&& IsFiniteBounded(value[2]);
	}

	static bool BoundsAreFiniteAndOrdered(vector boundsMin, vector boundsMax)
	{
		return IsFiniteVector(boundsMin)
			&& IsFiniteVector(boundsMax)
			&& boundsMin[0] <= boundsMax[0]
			&& boundsMin[1] <= boundsMax[1]
			&& boundsMin[2] <= boundsMax[2];
	}

	static float CircularYawDifference(float first, float second)
	{
		float difference = Math.AbsFloat(first - second);
		while (difference >= 360.0)
			difference -= 360.0;
		if (difference > 180.0)
			difference = 360.0 - difference;
		return Math.AbsFloat(difference);
	}

	static bool IsLowerHex64(string value)
	{
		if (value.Length() != 64)
			return false;
		for (int i = 0; i < value.Length(); i++)
		{
			int code = value.ToAscii(i);
			if (!((code >= 48 && code <= 57) || (code >= 97 && code <= 102)))
				return false;
		}
		return true;
	}

	static bool IsCanonicalUUID(string value)
	{
		if (value.Length() != 36)
			return false;
		for (int i = 0; i < value.Length(); i++)
		{
			if (i == 8 || i == 13 || i == 18 || i == 23)
			{
				if (value.ToAscii(i) != 45)
					return false;
				continue;
			}
			int code = value.ToAscii(i);
			if (!((code >= 48 && code <= 57) || (code >= 97 && code <= 102)))
				return false;
		}
		int variantCode = value.ToAscii(19);
		return variantCode == 56 || variantCode == 57 || variantCode == 97 || variantCode == 98;
	}

	static string ExpectedEntityName(string planId, int index)
	{
		return "RJMCP_" + planId + "_" + index.ToString();
	}

	static bool IsAllowedPrefab(string prefab)
	{
		// Exact production allowlist is intentionally empty at Checkpoint C.
		// Never replace this with Contains(), IndexOf(), a glob, or client input.
		return false;
	}

	static bool CardinalityIsExact(RJMCP_VegetationApplyRequest req)
	{
		return req.prefabs && req.prefabs.Count() == req.count
			&& req.entityNames && req.entityNames.Count() == req.count
			&& req.x && req.x.Count() == req.count
			&& req.y && req.y.Count() == req.count
			&& req.z && req.z.Count() == req.count
			&& req.yaw && req.yaw.Count() == req.count
			&& req.scale && req.scale.Count() == req.count;
	}

	static bool EntityMatches(
		WorldEditorAPI api,
		IEntitySource source,
		RJMCP_VegetationApplyRequest req,
		int index,
		int layerId
	)
	{
		if (!source || source.GetName() != req.entityNames[index])
			return false;
		if (source.GetSubScene() != req.subscene || source.GetLayerID() != layerId)
			return false;

		BaseContainer ancestor = source.GetAncestor();
		if (!ancestor || ancestor.GetResourceName() != req.prefabs[index])
			return false;

		IEntity entity = api.SourceToEntity(source);
		if (!entity)
			return false;
		vector position = entity.GetOrigin();
		vector angles = entity.GetAngles();
		float actualScale = entity.GetScale();
		if (!IsFiniteVector(position) || !IsFiniteVector(angles) || !IsFiniteBounded(actualScale))
			return false;
		if (Math.AbsFloat(position[0] - req.x[index]) > TRANSFORM_EPSILON
			|| Math.AbsFloat(position[1] - req.y[index]) > TRANSFORM_EPSILON
			|| Math.AbsFloat(position[2] - req.z[index]) > TRANSFORM_EPSILON
			|| CircularYawDifference(angles[0], req.yaw[index]) > TRANSFORM_EPSILON
			|| Math.AbsFloat(actualScale - req.scale[index]) > TRANSFORM_EPSILON)
			return false;
		return true;
	}

	static int CountEntitiesByName(WorldEditorAPI api, string name, out IEntitySource uniqueEntity)
	{
		uniqueEntity = null;
		int matchingNames = 0;
		int editorEntityCount = api.GetEditorEntityCount();
		for (int entityIndex = 0; entityIndex < editorEntityCount; entityIndex++)
		{
			IEntitySource candidate = api.GetEditorEntity(entityIndex);
			if (!candidate || candidate.GetName() != name)
				continue;
			matchingNames++;
			if (matchingNames == 1)
				uniqueEntity = candidate;
			else
				uniqueEntity = null;
		}
		return matchingNames;
	}

	static bool EntityNamesAreAbsent(WorldEditorAPI api, notnull array<string> createdNames)
	{
		for (int verifyIndex = 0; verifyIndex < createdNames.Count(); verifyIndex++)
		{
			IEntitySource ignoredEntity;
			if (CountEntitiesByName(api, createdNames[verifyIndex], ignoredEntity) != 0)
				return false;
		}
		return true;
	}

	static bool EntitySourcesAreAbsent(
		WorldEditorAPI api,
		notnull array<IEntitySource> created
	)
	{
		int editorEntityCount = api.GetEditorEntityCount();
		for (int entityIndex = 0; entityIndex < editorEntityCount; entityIndex++)
		{
			IEntitySource candidate = api.GetEditorEntity(entityIndex);
			for (int createdIndex = 0; createdIndex < created.Count(); createdIndex++)
			{
				if (candidate && candidate == created[createdIndex])
					return false;
			}
		}
		return true;
	}

	static bool CleanupCreated(WorldEditorAPI api, notnull array<IEntitySource> created)
	{
		bool deleted = true;
		for (int i = created.Count() - 1; i >= 0; i--)
		{
			if (!created[i] || !api.DeleteEntity(created[i]))
				deleted = false;
		}
		return deleted;
	}

	static bool VerifyExactBatch(
		WorldEditorAPI api,
		RJMCP_VegetationApplyRequest req,
		int layerId,
		out int matchingCount
	)
	{
		matchingCount = 0;
		for (int verifyIndex = 0; verifyIndex < req.count; verifyIndex++)
		{
			IEntitySource uniqueEntity;
			if (CountEntitiesByName(api, req.entityNames[verifyIndex], uniqueEntity) != 1)
				return false;
			if (!EntityMatches(api, uniqueEntity, req, verifyIndex, layerId))
				return false;
			matchingCount++;
		}
		return matchingCount == req.count;
	}

	override JsonApiStruct GetRequest()
	{
		return new RJMCP_VegetationApplyRequest();
	}

	override JsonApiStruct GetResponse(JsonApiStruct request)
	{
		RJMCP_VegetationApplyRequest req = RJMCP_VegetationApplyRequest.Cast(request);
		RJMCP_VegetationApplyResponse response = new RJMCP_VegetationApplyResponse();
		if (!req)
		{
			Fail(response, "INVALID_REQUEST", "Request could not be decoded");
			return response;
		}
		response.planId = req.planId;
		response.operationId = req.operationId;
		response.mode = req.mode;
		response.expectedCount = req.count;

		// Validate the complete trusted envelope before obtaining an editor action.
		if (req.mode != "create" && req.mode != "reconcile")
		{
			Fail(response, "INVALID_MODE", "mode must be create or reconcile");
			return response;
		}
		if (!IsLowerHex64(req.planId) || !IsCanonicalUUID(req.operationId))
		{
			Fail(response, "INVALID_OPERATION_ID", "planId or operationId is not canonical");
			return response;
		}
		if (req.bridgeBuildId != BRIDGE_BUILD_ID)
		{
			Fail(response, "BRIDGE_BUILD_MISMATCH", "Python and bridge build IDs differ");
			return response;
		}
		if (req.catalogHash != CATALOG_HASH)
		{
			Fail(response, "CATALOG_MISMATCH", "Python and bridge catalog hashes differ");
			return response;
		}
		if (req.worldPath != ALLOWED_WORLD)
		{
			Fail(response, "WORLD_NOT_ALLOWED", "Request world is not allowlisted");
			return response;
		}
		if (req.targetLayer != "MCP_Preview" && req.targetLayer != "MCP_Vegetation")
		{
			Fail(response, "LAYER_NOT_ALLOWED", "Target layer is not allowlisted");
			return response;
		}
		if (req.count < 1 || req.count > 100 || !CardinalityIsExact(req))
		{
			Fail(response, "INVALID_CARDINALITY", "All placement arrays must exactly match count 1..100");
			return response;
		}
		for (int validationIndex = 0; validationIndex < req.count; validationIndex++)
		{
			if (!IsAllowedPrefab(req.prefabs[validationIndex]))
			{
				Fail(response, "CATALOG_NOT_READY", "Production vegetation allowlist is empty/unverified");
				return response;
			}
			if (req.entityNames[validationIndex] != ExpectedEntityName(req.planId, validationIndex))
			{
				Fail(response, "INVALID_ENTITY_NAME", "Entity name is not derived from planId and index");
				return response;
			}
			if (!IsFiniteBounded(req.x[validationIndex]) || !IsFiniteBounded(req.y[validationIndex])
				|| !IsFiniteBounded(req.z[validationIndex]) || !IsFiniteBounded(req.yaw[validationIndex])
				|| !IsFiniteBounded(req.scale[validationIndex])
				|| req.yaw[validationIndex] < 0 || req.yaw[validationIndex] >= 360)
			{
				Fail(response, "INVALID_TRANSFORM", "Placement transform is invalid or out of range");
				return response;
			}
		}
		WorldEditor worldEditor = Workbench.GetModule(WorldEditor);
		if (!worldEditor)
		{
			Fail(response, "WORLD_EDITOR_NOT_RUNNING", "WorldEditor module is not running");
			return response;
		}
		WorldEditorAPI api = worldEditor.GetApi();
		if (!api)
		{
			Fail(response, "WORLD_EDITOR_API_UNAVAILABLE", "WorldEditorAPI is unavailable");
			return response;
		}

		string actualWorld;
		api.GetWorldPath(actualWorld);
		if (actualWorld != req.worldPath || api.GetCurrentSubScene() != req.subscene)
		{
			Fail(response, "STALE_WORLD", "World or subscene changed after planning");
			return response;
		}

		int layerId = api.GetSubsceneLayerId(req.subscene, req.targetLayer);
		if (layerId < 0 || api.GetSubsceneLayerPath(req.subscene, layerId) != req.targetLayer)
		{
			Fail(response, "LAYER_NOT_FOUND", "Exact root-level target layer does not exist");
			return response;
		}

		// Reconciliation deliberately stops after common identity/world/subscene/
		// layer-existence checks. It observes deterministic entities even when the
		// layer became locked, terrain changed, or the editor is temporarily busy.
		int presentCount = 0;
		bool entityConflict = false;
		for (int existingIndex = 0; existingIndex < req.count; existingIndex++)
		{
			IEntitySource existing;
			int nameMultiplicity = CountEntitiesByName(
				api,
				req.entityNames[existingIndex],
				existing
			);
			if (nameMultiplicity == 0)
				continue;
			presentCount += nameMultiplicity;
			if (nameMultiplicity == 1 && EntityMatches(api, existing, req, existingIndex, layerId))
				response.matchingCount++;
			else
				entityConflict = true;
		}
		if (entityConflict)
		{
			Fail(response, "ENTITY_CONFLICT", "A deterministic entity exists with changed data", "CHANGED");
			return response;
		}
		if (presentCount > 0 && response.matchingCount != req.count)
		{
			Fail(response, "PARTIAL_OPERATION", "Only part of the deterministic batch exists", "PARTIAL");
			return response;
		}
		if (response.matchingCount == req.count)
		{
			response.status = "ok";
			response.errorCode = "";
			response.state = "COMPLETE";
			response.message = "Full matching batch already exists";
			return response;
		}
		if (req.mode == "reconcile")
		{
			response.status = "ok";
			response.errorCode = "";
			response.state = "NONE";
			response.message = "No deterministic entities exist; create was not retried";
			return response;
		}

		// Hard compile/live safety gate. Every create request fails before any
		// create-only editor-state, terrain, spacing, or mutation operation.
		if (!MUTATION_IMPLEMENTATION_VALIDATED)
		{
			Fail(
				response,
				"MUTATION_IMPLEMENTATION_UNVALIDATED",
				"Create mutation is disabled until live implementation validation succeeds"
			);
			return response;
		}

		if (!IsFiniteBounded(req.minSpacingM) || req.minSpacingM <= 0
			|| !IsFiniteBounded(req.maxSlopeDeg) || req.maxSlopeDeg < 0 || req.maxSlopeDeg >= 90
			|| !IsFiniteBounded(req.scaleMin) || !IsFiniteBounded(req.scaleMax)
			|| req.scaleMin <= 0 || req.scaleMin > req.scaleMax || req.scaleMax > 10
			|| !IsFiniteBounded(req.terrainYEpsilon) || req.terrainYEpsilon <= 0 || req.terrainYEpsilon > 0.05)
		{
			Fail(response, "INVALID_CONSTRAINT", "Numeric constraints are invalid");
			return response;
		}
		if (req.scaleMin != 1.0 || req.scaleMax != 1.0)
		{
			Fail(response, "UNSUPPORTED_SCALE", "Unvalidated mutation path permits only scale 1");
			return response;
		}
		for (int scaleIndex = 0; scaleIndex < req.count; scaleIndex++)
		{
			if (req.scale[scaleIndex] != 1.0)
			{
				Fail(response, "UNSUPPORTED_SCALE", "Unvalidated mutation path permits only scale 1");
				return response;
			}
		}

		if (api.IsGameMode())
		{
			Fail(response, "EDIT_MODE_REQUIRED", "WorldEditor must be in normal edit mode");
			return response;
		}
		if (api.IsPrefabEditMode())
		{
			Fail(response, "PREFAB_MODE_FORBIDDEN", "Prefab edit mode is forbidden");
			return response;
		}
		if (api.IsDoingEditAction() || api.UndoOrRedoIsRestoring())
		{
			Fail(response, "EDITOR_BUSY", "Another edit action or Undo/Redo is active");
			return response;
		}
		if (api.IsEntityLayerLockedHierarchy(req.subscene, layerId))
		{
			Fail(response, "LAYER_LOCKED", "Target layer or its parent hierarchy is locked");
			return response;
		}

		vector boundsMin;
		vector boundsMax;
		if (!worldEditor.GetTerrainBounds(boundsMin, boundsMax))
		{
			Fail(response, "TERRAIN_UNAVAILABLE", "Terrain bounds are unavailable");
			return response;
		}
		if (!BoundsAreFiniteAndOrdered(boundsMin, boundsMax))
		{
			Fail(response, "INVALID_TERRAIN_BOUNDS", "Terrain bounds are non-finite or unordered");
			return response;
		}
		BaseWorld world = api.GetWorld();
		if (!world)
		{
			Fail(response, "WORLD_UNAVAILABLE", "WorldEditor world is unavailable");
			return response;
		}
		float minimumNormalY = Math.Cos(req.maxSlopeDeg * DEG_TO_RAD);
		float spacingSquared = req.minSpacingM * req.minSpacingM;
		for (int terrainIndex = 0; terrainIndex < req.count; terrainIndex++)
		{
			if (req.x[terrainIndex] < boundsMin[0] || req.x[terrainIndex] > boundsMax[0]
				|| req.z[terrainIndex] < boundsMin[2] || req.z[terrainIndex] > boundsMax[2])
			{
				Fail(response, "OUTSIDE_TERRAIN_BOUNDS", "Placement is outside terrain bounds");
				return response;
			}
			float currentY;
			if (!api.TryGetTerrainSurfaceY(req.x[terrainIndex], req.z[terrainIndex], currentY)
				|| !IsFiniteBounded(currentY)
				|| Math.AbsFloat(currentY - req.y[terrainIndex]) > req.terrainYEpsilon)
			{
				Fail(response, "STALE_TERRAIN", "Terrain height changed after planning");
				return response;
			}
			vector normalPosition = Vector(req.x[terrainIndex], currentY, req.z[terrainIndex]);
			vector normal = SCR_TerrainHelper.GetTerrainNormal(normalPosition, world);
			float normalLength = Math.Sqrt(
				normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]
			);
			if (!IsFiniteVector(normalPosition)
				|| Math.AbsFloat(normalPosition[1] - currentY) > req.terrainYEpsilon
				|| !IsFiniteVector(normal)
				|| !IsFiniteBounded(normalLength)
				|| normalLength <= 0.000001)
			{
				Fail(response, "STALE_TERRAIN", "Terrain normal/slope changed after planning");
				return response;
			}
			float normalizedY = normal[1] / normalLength;
			if (!IsFiniteBounded(normalizedY) || normalizedY < minimumNormalY)
			{
				Fail(response, "STALE_TERRAIN", "Terrain normal/slope changed after planning");
				return response;
			}

			for (int priorIndex = 0; priorIndex < terrainIndex; priorIndex++)
			{
				float deltaX = req.x[terrainIndex] - req.x[priorIndex];
				float deltaZ = req.z[terrainIndex] - req.z[priorIndex];
				if (deltaX * deltaX + deltaZ * deltaZ < spacingSquared)
				{
					Fail(response, "SPACING_VIOLATION", "Planned placements violate minimum spacing");
					return response;
				}
			}
		}

		if (!api.BeginEntityAction("RJMCP vegetation " + req.planId.Substring(0, 12)))
		{
			Fail(response, "BEGIN_ACTION_FAILED", "BeginEntityAction returned false");
			return response;
		}

		array<IEntitySource> created = {};
		array<string> createdNames = {};
		bool createFailed = false;
		for (int createIndex = 0; createIndex < req.count; createIndex++)
		{
			vector position = Vector(req.x[createIndex], req.y[createIndex], req.z[createIndex]);
			vector angles = Vector(req.yaw[createIndex], 0, 0);
			IEntitySource source = api.CreateEntity(
				req.prefabs[createIndex],
				req.entityNames[createIndex],
				layerId,
				null,
				position,
				angles
			);
			if (!source)
			{
				createFailed = true;
				break;
			}
			// The current entity immediately joins cleanup responsibility.
			created.Insert(source);
			createdNames.Insert(source.GetName());
			response.m_aEntityNames.Insert(req.entityNames[createIndex]);
			response.createdCount++;

			if (source.GetName() != req.entityNames[createIndex])
			{
				if (!api.RenameEntity(source, req.entityNames[createIndex]))
				{
					createFailed = true;
					break;
				}
				createdNames.Insert(source.GetName());
			}
			if (!EntityMatches(api, source, req, createIndex, layerId))
			{
				createFailed = true;
				break;
			}
		}

		if (createFailed)
		{
			bool cleanupSucceeded = CleanupCreated(api, created);
			bool actionEnded = api.EndEntityAction("RJMCP vegetation rollback");
			if (!actionEnded)
			{
				response.rollbackVerified = false;
				Fail(response, "UNKNOWN_OUTCOME", "Create failed and the editor action did not end", "UNKNOWN");
				return response;
			}
			bool absentAfterEnd = EntityNamesAreAbsent(api, createdNames);
			bool sourcesAbsentAfterEnd = EntitySourcesAreAbsent(api, created);
			response.rollbackVerified = cleanupSucceeded && absentAfterEnd && sourcesAbsentAfterEnd;
			if (response.rollbackVerified)
				Fail(response, "CREATE_FAILED", "Create failed and rollback was verified", "ROLLBACK_VERIFIED");
			else
				Fail(response, "ROLLBACK_FAILED", "Create failed and full rollback was not proven", "ROLLBACK_FAILED");
			return response;
		}

		bool actionEnded = api.EndEntityAction("RJMCP vegetation " + req.planId.Substring(0, 12));
		if (!actionEnded)
		{
			response.rollbackVerified = false;
			Fail(response, "UNKNOWN_OUTCOME", "End failed; no further mutation was attempted", "UNKNOWN");
			return response;
		}

		int postEndMatchingCount;
		if (!VerifyExactBatch(api, req, layerId, postEndMatchingCount))
		{
			response.matchingCount = postEndMatchingCount;
			response.rollbackVerified = false;
			Fail(
				response,
				"POST_COMMIT_VERIFICATION_FAILED",
				"The completed editor action did not round-trip to one exact deterministic batch",
				"UNKNOWN"
			);
			return response;
		}

		response.status = "ok";
		response.errorCode = "";
		response.state = "COMPLETE";
		response.matchingCount = postEndMatchingCount;
		response.message = "Created one unsaved vegetation batch; user must save manually";
		return response;
	}
}
