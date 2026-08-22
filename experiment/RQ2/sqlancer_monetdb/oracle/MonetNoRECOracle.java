/*
 * RQ2 MonetDB port: NoREC test oracle.
 *
 * The MonetDB provider, AST, schema, and generator classes are obtained from
 * the pinned MonetDB SQLancer fork by the accompanying Dockerfile.
 */
package sqlancer.monet.oracle;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

import sqlancer.IgnoreMeException;
import sqlancer.Randomly;
import sqlancer.common.oracle.NoRECBase;
import sqlancer.common.query.SQLQueryAdapter;
import sqlancer.common.query.SQLancerResultSet;
import sqlancer.monet.MonetCompoundDataType;
import sqlancer.monet.MonetGlobalState;
import sqlancer.monet.MonetSchema;
import sqlancer.monet.MonetSchema.MonetColumn;
import sqlancer.monet.MonetSchema.MonetDataType;
import sqlancer.monet.MonetSchema.MonetTable;
import sqlancer.monet.MonetSchema.MonetTables;
import sqlancer.monet.MonetVisitor;
import sqlancer.monet.ast.MonetCastOperation;
import sqlancer.monet.ast.MonetColumnValue;
import sqlancer.monet.ast.MonetExpression;
import sqlancer.monet.ast.MonetJoin;
import sqlancer.monet.ast.MonetJoin.MonetJoinType;
import sqlancer.monet.ast.MonetPostfixText;
import sqlancer.monet.ast.MonetQuery;
import sqlancer.monet.ast.MonetQuery.MonetSubquery;
import sqlancer.monet.ast.MonetSelect;
import sqlancer.monet.ast.MonetSelect.MonetFromTable;
import sqlancer.monet.ast.MonetSelect.SelectType;
import sqlancer.monet.gen.MonetCommon;
import sqlancer.monet.gen.MonetExpressionGenerator;
import sqlancer.monet.gen.MonetRandomQueryGenerator;

public class MonetNoRECOracle extends NoRECBase<MonetGlobalState> {

    private final MonetSchema s;

    public MonetNoRECOracle(MonetGlobalState globalState) {
        super(globalState);
        this.s = globalState.getSchema();
        MonetCommon.addCommonExpressionErrors(errors);
        MonetCommon.addCommonFetchErrors(errors);
        MonetCommon.addGroupingErrors(errors);
    }

    @Override
    public void check() throws SQLException {
        MonetExpressionGenerator gen = new MonetExpressionGenerator(state);
        MonetTables randomTables = s.getRandomTableNonEmptyTables();
        List<MonetColumn> columns = randomTables.getColumns();
        List<MonetTable> tables = randomTables.getTables();

        gen.setTables(randomTables);
        gen.setColumns(columns);
        List<MonetJoin> joinStatements = getJoinStatements(state, gen, tables);
        List<MonetExpression> fromTables = tables.stream().map(t -> new MonetFromTable(t, null))
                .collect(Collectors.toList());
        MonetExpression randomWhereCondition = gen.generateExpression(0, MonetDataType.BOOLEAN);
        int secondCount = getUnoptimizedQueryCount(fromTables, randomWhereCondition, joinStatements);
        int firstCount = getOptimizedQueryCount(gen, fromTables, columns, randomWhereCondition, joinStatements);
        if (firstCount == -1 || secondCount == -1) {
            throw new IgnoreMeException();
        }
        if (firstCount != secondCount) {
            String queryFormatString = "-- %s;\n-- count: %d";
            String firstQueryStringWithCount = String.format(queryFormatString, optimizedQueryString, firstCount);
            String secondQueryStringWithCount = String.format(queryFormatString, unoptimizedQueryString, secondCount);
            state.getState().getLocalState()
                    .log(String.format("%s\n%s", firstQueryStringWithCount, secondQueryStringWithCount));
            String assertionMessage = String.format("the counts mismatch (%d and %d)!\n%s\n%s", firstCount, secondCount,
                    firstQueryStringWithCount, secondQueryStringWithCount);
            throw new AssertionError(assertionMessage);
        }
    }

    public static List<MonetJoin> getJoinStatements(MonetGlobalState globalState, MonetExpressionGenerator gen,
            List<MonetTable> tables) {
        int njoins = Randomly.fromOptions(0, 1, 1, 2);

        List<MonetJoin> joinStatements = new ArrayList<>(njoins);
        if (tables.size() == 1) {
            for (int n = 0; n < njoins; n++) {
                MonetJoinType jt = MonetJoinType.getRandom();
                boolean isLateral = jt != MonetJoinType.RIGHT && jt != MonetJoinType.FULL
                        && Randomly.fromOptions(1, 1, 2, 2, 2, 2) == 1; /* 33% */
                int nrColumns = Randomly.smallNumber() + 1;
                List<MonetDataType> stypes = Randomly.nonEmptySubsetPotentialDuplicates(MonetDataType.aLLTYPES,
                        nrColumns);
                List<MonetColumn> ngencols = null;

                /* lateral join queries can see previous generated queries on this loop */
                if (isLateral) {
                    ngencols = new ArrayList<>(gen.getColumns().size());
                    for (MonetColumn x : gen.getColumns()) {
                        ngencols.add(x);
                    }
                }
                MonetQuery q = MonetRandomQueryGenerator.createRandomQuery(globalState, 0, ngencols, stypes, false,
                        false, false, false);
                String name = String.format("nort%d", n);
                List<MonetColumn> subcols = new ArrayList<>();
                int coln = 0;
                for (MonetExpression ex : q.getFetchColumns()) {
                    String nextColumnName = String.format("norc%d", coln);
                    MonetDataType dt = ex.getExpressionType();
                    if (dt == null) {
                        throw new AssertionError("Ups " + ex.getClass().getName()); /* this is for debugging */
                    }
                    subcols.add(new MonetColumn(nextColumnName, dt, name));
                    coln++;
                }
                MonetSubquery subquery = new MonetSubquery(q, name, null, subcols);
                List<MonetColumn> cols = gen.getColumns();
                cols.addAll(subquery.getColumns());
                gen.setColumns(cols);

                int nclauses = Randomly.fromOptions(1, 2, 3);
                List<MonetExpression> joinclauses = new ArrayList<>(nclauses);
                if (jt != MonetJoinType.CROSS && jt != MonetJoinType.NATURAL) {
                    for (int k = 0; k < nclauses; k++) {
                        joinclauses.add(gen.generateExpression(0, MonetDataType.BOOLEAN));
                    }
                }

                joinStatements.add(new MonetJoin(subquery, joinclauses, jt, isLateral));
            }
        }
        return joinStatements;
    }

    private int getUnoptimizedQueryCount(List<MonetExpression> fromTables, MonetExpression randomWhereCondition,
            List<MonetJoin> joinStatements) throws SQLException {
        MonetSelect select = new MonetSelect();
        MonetCastOperation isTrue = new MonetCastOperation(randomWhereCondition,
                MonetCompoundDataType.create(MonetDataType.INT));
        MonetPostfixText asText = new MonetPostfixText(isTrue, " as count", null, MonetDataType.INT);
        select.setFetchColumns(Arrays.asList(asText));
        select.setFromList(fromTables);
        select.setSelectType(SelectType.ALL);
        select.setJoinClauses(joinStatements);
        int secondCount = 0;
        unoptimizedQueryString = "SELECT CAST(SUM(count) AS BIGINT) FROM (" + MonetVisitor.asString(select)
                + ") as res";
        if (options.logEachSelect()) {
            logger.writeCurrent(unoptimizedQueryString);
        }
        errors.add("canceling statement due to statement timeout");
        SQLQueryAdapter q = new SQLQueryAdapter(unoptimizedQueryString, errors);
        SQLancerResultSet rs;
        try {
            rs = q.executeAndGet(state);
        } catch (Exception e) {
            throw new AssertionError(unoptimizedQueryString, e);
        }
        if (rs == null) {
            return -1;
        }
        if (rs.next()) {
            secondCount += rs.getLong(1);
        }
        rs.close();
        return secondCount;
    }

    private int getOptimizedQueryCount(MonetExpressionGenerator gen, List<MonetExpression> randomTables,
            List<MonetColumn> columns, MonetExpression randomWhereCondition, List<MonetJoin> joinStatements)
            throws SQLException {
        MonetSelect select = new MonetSelect();
        MonetColumnValue allColumns = new MonetColumnValue(Randomly.fromList(columns), null);
        select.setFetchColumns(Arrays.asList(allColumns));
        select.setFromList(randomTables);
        select.setWhereClause(randomWhereCondition);
        if (Randomly.getBooleanWithSmallProbability()) {
            select.setOrderByExpressions(gen.generateOrderBy());
        }
        select.setSelectType(SelectType.ALL);
        select.setJoinClauses(joinStatements);
        int firstCount = 0;
        try (Statement stat = con.createStatement()) {
            optimizedQueryString = MonetVisitor.asString(select);
            if (options.logEachSelect()) {
                logger.writeCurrent(optimizedQueryString);
            }
            try (ResultSet rs = stat.executeQuery(optimizedQueryString)) {
                while (rs.next()) {
                    firstCount++;
                }
            }
        } catch (SQLException e) {
            throw new IgnoreMeException();
        }
        return firstCount;
    }

}
